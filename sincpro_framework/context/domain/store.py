"""`ContextStore`: the one place the context is kept outside the process — a flow to resume, a
session, a tenant's defaults, the process level every replica reads, and what the framework keeps
beside them with a version: drafts and pipeline runs.

    store.keep("tenant:acme", {"lang": "es", "plan": "pro"})
    store.keep(f"session:{sid}", {"user_id": "ana"}, ttl=timedelta(hours=8))
    store.restore("tenant:acme")   →  {"lang": "es", "plan": "pro"}       None when gone
    store.version("tenant:acme")   →  2                                    0 when never kept

    entry = store.keep_entry("SINCPRO_ENTITY_DRAFT_CONTEXT:…:billing.Invoice%3A7", values, 0)
    store.keep_entry(entry.key, newer, expected=entry.version)     EntryConflict if another came first
    store.entries("SINCPRO_ENTITY_DRAFT_CONTEXT:…")                the live ones of a folder

**Not a cache.** A cache keeps what can be computed again; this keeps what a flow *is*, written by
the flow and read by whoever continues it. Opt-in: by default a context travels with its message.

**One store, everything the context keeps.** A project sets one — the process's memory
(`InMemoryContexts`), a Redis, Valkey or Memcached (`KeyValueContexts`), or a store of its own — and
drafts and pipeline runs are kept there, with nothing else to set. Each is an *entry*: a key, its
values as JSON, a version won atomically and a TTL. A store that keeps no entries says so when one
is asked of it.
"""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from sincpro_framework.context.domain import exceptions
from sincpro_framework.sincpro_abstractions import DataTransferObject


class Entry(DataTransferObject):
    """One thing kept beside the contexts with a version: a draft, a pipeline run."""

    key: str
    values: dict[str, Any]
    """The values as JSON carries them — `"50.00"` for a decimal, ISO text for a date."""
    version: int
    """What the next keep names as `expected`. Opaque, like an ETag: it never repeats for a key,
    and it is not a count of keeps."""
    kept_at: datetime


class ContextStore(ABC):
    @abstractmethod
    def keep(self, key: str, values: Mapping[str, Any], ttl: timedelta | None = None) -> None:
        """`values` under `key`, replacing what was there, until `ttl` passes — forever without
        one. Each keep moves the key's version."""

    @abstractmethod
    def restore(self, key: str) -> dict[str, Any] | None:
        """What `key` holds, or `None` when nothing was kept or its time passed."""

    @abstractmethod
    def forget(self, key: str) -> None:
        """`key` is gone."""

    @abstractmethod
    def version(self, key: str) -> int:
        """How many times `key` was kept — what a reader compares to know it changed."""

    def _keeps_no_entries(self) -> exceptions.ProgrammingError:
        return exceptions.ProgrammingError(
            f"{type(self).__name__} keeps no entries — the drafts and pipeline runs the context "
            "keeps: implement keep_entry, entry, discard_entry and entries, or keep the context "
            "in InMemoryContexts or KeyValueContexts"
        )

    def keep_entry(self, key: str, values: Mapping[str, Any], expected: int) -> Entry:
        """`values` under `key` with a new version, when `expected` is the version there now —
        `0` when there is none; anything else raises `EntryConflict`. The TTL starts again."""
        raise self._keeps_no_entries()

    def entry(self, key: str) -> Entry | None:
        """The live entry under `key`, or `None` when there is none, it was discarded or it
        expired."""
        raise self._keeps_no_entries()

    def discard_entry(self, key: str) -> None:
        """Drop the entry under `key`, its version spent: a writer holding it cannot bring it
        back. One with none is not an error."""
        raise self._keeps_no_entries()

    def entries(self, folder: str) -> list[str]:
        """The keys of the live entries in `folder` — everything before a key's last `:` — the
        most recently kept first."""
        raise self._keeps_no_entries()
