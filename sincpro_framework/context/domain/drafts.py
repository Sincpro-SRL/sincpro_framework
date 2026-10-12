"""Drafts: what a form keeps between requests before anything is saved — a context kept by whose
it is, guarded by its version.

    drafts = use_context().drafts()                       this tenant's, this user's
    draft = drafts.keep("invoice:01a1", values, origin_version=invoice.version, expected=0)
    draft = drafts.read("invoice:01a1")                   the latest, or None
    drafts.keep("invoice:01a1", newer, invoice.version, expected=draft.version)
                                                          DraftConflict if another tab kept in between
    drafts.discard("invoice:01a1")

Context: optional, and never a lock. Each keep names the version it read — `0` when there is no live
draft — and loses with `DraftConflict` when another keep came first, so two tabs never overwrite
each other in silence. Every draft has a TTL, renewed on each keep, so an abandoned one disappears
on its own. A version is an opaque number that never repeats for a key — not after a discard, not
after an expiry — so a tab still holding an old one cannot win.

The draft is not the record. Activating it is the project's Command: read the stored record,
`refuse_stale(draft, record)` (`sincpro_framework.ddd`) — a record changed since the draft started
is `StaleAggregate` — then `assign(record, draft.values)`, save it and discard the draft.
"""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from pydantic_core import to_jsonable_python

from sincpro_framework.exceptions import ClientError, FailureKind
from sincpro_framework.sincpro_abstractions import DataTransferObject


class Draft(DataTransferObject):
    """One kept draft: the form's values, as JSON, and the versions that guard them."""

    key: str
    values: dict[str, Any]
    """The values as JSON carries them — `"50.00"` for a decimal, ISO text for a date. Read
    back onto a record with the field types, as any value from a form."""
    version: int
    """This draft's own version: what the next keep names as `expected`. Opaque, like an
    ETag: it never repeats for the key, and it is not a count of keeps."""
    origin_version: int
    """The stored record's version when the draft started (`0` for a new record): what
    `refuse_stale` compares with the record read at activation."""
    kept_at: datetime


class DraftConflict(ClientError):
    """A keep named a version that is no longer the draft's: another tab, another device, kept
    in between, or the draft expired. The caller read before someone else wrote: read again,
    merge or reload, and keep with the version read.

    `current` is the version there now — `0` when there is no live draft.
    """

    failure_kind = FailureKind.CONFLICT

    def __init__(self, key: str, expected: int, current: int) -> None:
        super().__init__(
            f"draft {key} is at version {current}, not {expected}: read it again before keeping"
        )
        self.key = key
        self.expected = expected
        self.current = current


class DraftStore(ABC):
    """Where drafts are kept: each by whose it is (`owner`, what a `Drafts` reads from the context)
    and what it is about (`subject`). Each `ContextStore` gives the one kept beside it
    (`ContextStore.drafts()`).

    An adapter implements the four abstract methods over one whole key; whose and what are put
    together here, so every store keys a draft the same way.
    """

    def keep(
        self,
        owner: str,
        subject: str,
        values: Mapping[str, Any],
        origin_version: int,
        expected: int,
    ) -> Draft:
        """`values` as the draft of `subject` for `owner`, under a new version.

            in      "user_id=ana", "invoice:7", {"discount": Decimal("50")}, origin 3, expected 0
            out     Draft(version=…, origin_version=3, values={"discount": "50"})

        `expected` is the version the caller read, `0` for none; anything else raises
        `DraftConflict`. The TTL starts again. The draft answered is the one `read` answers
        until the next keep.
        """
        return self._keep(f"{owner}/{subject}", values, origin_version, expected)

    def read(self, owner: str, subject: str) -> Draft | None:
        """The live draft of `subject` for `owner`, or `None` when there is none, it was
        discarded or it expired."""
        return self._read(f"{owner}/{subject}")

    def discard(self, owner: str, subject: str) -> None:
        """Drop the draft of `subject` for `owner`; one with none is not an error."""
        self._discard(f"{owner}/{subject}")

    @abstractmethod
    def subjects(self, owner: str) -> list[str]:
        """What `owner` has a live draft of, the most recently kept first."""

    @abstractmethod
    def _keep(
        self, key: str, values: Mapping[str, Any], origin_version: int, expected: int
    ) -> Draft: ...

    @abstractmethod
    def _read(self, key: str) -> Draft | None: ...

    @abstractmethod
    def _discard(self, key: str) -> None: ...

    def _milliseconds(self, moment: datetime) -> int:
        """Where a chain started from nothing begins: the clock, above any version a chain that
        ended a TTL ago could have reached."""
        return int(moment.timestamp() * 1000)

    def _check(self, key: str, expected: int, current: int, live: bool) -> None:
        """A keep wins only over what it read: the live version, or `0` when none is live."""
        if expected != (current if live else 0):
            raise DraftConflict(key, expected, current if live else 0)

    def _drafted(
        self,
        key: str,
        values: Mapping[str, Any],
        version: int,
        origin_version: int,
        now: datetime,
    ) -> Draft:
        return Draft(
            key=key,
            values=to_jsonable_python(dict(values)),
            version=version,
            origin_version=origin_version,
            kept_at=now,
        )
