"""Versioned documents per scope — how what an administrator changes at runtime is kept: one
document for what every tenant shares (`None`), one per tenant, each written only over the version
it was read at.

    roles: ScopedDocuments[DefinedRoles]
    written = rewritten(roles, "bo", lambda current: current.replacing(definition))

Context: a write that finds another came first reads again and is asked again — the change is
judged over what is there now, never over what was read before. Five collisions in a row are a
`ConcurrentChange` (409): the administrator reads again.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable, Hashable
from typing import Protocol

from sincpro_framework.auth.common.domain import exceptions


class Versioned(Protocol):
    """A document that says the version it was written under."""

    @property
    def version(self) -> int:
        """1 for the first written; 0 before any."""
        ...


class ScopedDocuments[D](ABC):
    """The port: one document per scope, written only over the version before it."""

    @abstractmethod
    def read(self, scope: str | None = None) -> D:
        """The scope's document — the empty one, version 0, before the first write."""

    @abstractmethod
    def write(self, scope: str | None, document: D) -> bool:
        """Keep `document` as the scope's — only when the version kept now is the one before
        `document.version`, atomically. `False` when another write came first."""


def fingerprint(*documents: Versioned) -> tuple[Hashable, ...]:
    """What tells documents apart — each one's version and content: a store that lost a document
    (an eviction, a flush) writes its versions again from 1, with other content, and what was
    built from the lost one is never taken for it."""
    return tuple((one.version, hash(repr(one))) for one in documents)


def rewritten[D](
    documents: ScopedDocuments[D],
    scope: str | None,
    change: Callable[[D], D],
    attempts: int = 5,
) -> D:
    """The scope's document after `change`, written over the version it was read at — read and
    asked again while another write came first; `ConcurrentChange` after `attempts`."""
    for _ in range(attempts):
        current = documents.read(scope)
        document = change(current)
        if documents.write(scope, document):
            return document
    raise exceptions.ConcurrentChange(
        "another administrator changed it at the same moment: read it again"
    )
