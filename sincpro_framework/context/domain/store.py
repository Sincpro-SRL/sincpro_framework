"""`ContextStore`: the one place the context is saved outside the process — a flow to resume, a
session, a tenant's defaults, the process level every replica reads, and what the framework saves
beside them with a version: drafts and pipeline runs.

    store.save("tenant:acme", {"lang": "es", "plan": "pro"})
    store.save(f"session:{sid}", {"user_id": "ana"}, ttl=timedelta(hours=8))
    store.load("tenant:acme")   →  {"lang": "es", "plan": "pro"}       None when gone
    store.version("tenant:acme")   →  2                                    0 when never saved

    entry = store.save_entry("SINCPRO_ENTITY_DRAFT_CONTEXT:…:billing.Invoice%3A7", values, 0)
    store.save_entry(entry.key, newer, expected=entry.version)     EntryConflict if another came first
    store.list_entries("SINCPRO_ENTITY_DRAFT_CONTEXT:…")                the live ones of a folder

**Not a cache.** A cache stores what can be computed again; this stores what a flow *is*, written by
the flow and read by whoever continues it. Opt-in: by default a context travels with its message.

**One store, everything the context saves.** A project sets one — the process's memory
(`InMemoryContexts`), a Redis, Valkey or Memcached (`KeyValueContexts`), or a store of its own — and
drafts, pipeline runs and leases are saved there, with nothing else to set. Each is an *entry*: a key, its
values as JSON, a version won atomically and a TTL. A store that saves no entries says so when one
is asked of it.

**Beside it, on the same `KeyValueStore`** (`key_value_store()`): what is saved for the service
but is not what a flow is — leases, circuits, caches, idempotency records — each in a section
of its own. A cache is never an entry of the context; it lives where the context lives.
"""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from sincpro_framework.context.domain import exceptions
from sincpro_framework.data_layer.store import KeyValueStore, Leases
from sincpro_framework.sincpro_abstractions import DataTransferObject


class Entry(DataTransferObject):
    """One thing saved beside the contexts with a version: a draft, a pipeline run."""

    key: str
    values: dict[str, Any]
    """The values as JSON carries them — `"50.00"` for a decimal, ISO text for a date."""
    version: int
    """What the next save names as `expected`. Opaque, like an ETag: it never repeats for a key,
    and it is not a count of saves."""
    saved_at: datetime


class ContextStore(ABC):
    """The port a project implements to store the context outside the process — contexts by key
    (`save`/`load`/`delete`/`version`), and versioned entries beside them (`save_entry`/
    `get_entry`/`delete_entry`/`list_entries`) for drafts and pipeline runs.

    Only the four context methods are required: a store that saves no entries, leases or
    key-value section still serves a context shared across replicas, and says what it lacks
    the moment a draft or a pipeline run asks for it — never at wiring time. Set for the
    process with `store_context_in(store)`, for one bus with `bus.store_context_in(store)`.
    """

    @abstractmethod
    def save(self, key: str, values: Mapping[str, Any], ttl: timedelta | None = None) -> None:
        """`values` under `key`, replacing what was there, until `ttl` passes — forever without
        one. Each save moves the key's version."""

    @abstractmethod
    def load(self, key: str) -> dict[str, Any] | None:
        """What `key` holds, or `None` when nothing was saved or its time passed."""

    @abstractmethod
    def delete(self, key: str) -> None:
        """`key` is gone."""

    @abstractmethod
    def version(self, key: str) -> int:
        """How many times `key` was saved — what a reader compares to know it changed."""

    def _saves_no_entries(self) -> exceptions.ProgrammingError:
        """The error an entry method of a store without entries raises — naming the store,
        what it lacks, and the two ways out."""
        return exceptions.ProgrammingError(
            f"{type(self).__name__} saves no entries — the drafts and pipeline runs the context "
            "saves: implement save_entry, get_entry, delete_entry and list_entries, or save the context "
            "in InMemoryContexts or KeyValueContexts"
        )

    def save_entry(self, key: str, values: Mapping[str, Any], expected: int) -> Entry:
        """`values` under `key` with a new version, when `expected` is the version there now —
        `0` when there is none; anything else raises `EntryConflict`. The TTL starts again."""
        raise self._saves_no_entries()

    def get_entry(self, key: str) -> Entry | None:
        """The live entry under `key`, or `None` when there is none, it was discarded or it
        expired."""
        raise self._saves_no_entries()

    def delete_entry(self, key: str) -> None:
        """Drop the entry under `key`, its version spent: a writer holding it cannot bring it
        back. One with none is not an error."""
        raise self._saves_no_entries()

    def list_entries(self, folder: str) -> list[str]:
        """The keys of the live entries in `folder` — everything before a key's last `:` — the
        most recently saved first."""
        raise self._saves_no_entries()

    def leases(self) -> Leases:
        """The leases saved beside the contexts — a name held by one owner at a time, by every
        replica that shares this store."""
        raise exceptions.ProgrammingError(
            f"{type(self).__name__} saves no leases: save the context in InMemoryContexts or "
            "KeyValueContexts, or implement leases()"
        )

    def key_value_store(self) -> KeyValueStore:
        """The `KeyValueStore` this store stands on — where what is saved beside the contexts
        lives: leases, circuits, caches, idempotency records, each in a section of its own.
        Not a context's entry: a cache is never what a flow *is*. The store its leases stand
        on, unless a store says otherwise."""
        return self.leases().store
