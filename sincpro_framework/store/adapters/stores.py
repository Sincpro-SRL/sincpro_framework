"""The context stores the framework ships.

    InMemoryContexts()                                  one process: tests, a single worker
    KeyValueContexts(RedisKeyValue(redis))              every replica and service sharing Redis
    KeyValueContexts(store, TypedCodec(SIATContext))    the types back, the same codebase

Each keeps, beside the contexts, the entries the framework keeps with a version — the drafts and the
pipeline runs: in the same memory, or on the same `KeyValueStore` under `{prefix}:entries`,
each lasting `entry_ttl` from its last keep. Nothing else is set for them.

`KeyValueContexts` stands on `KeyValueStore`, so Redis, Valkey and Memcached are the adapters
`sincpro_framework.data_layer.caching` already has — behind their extras, nothing new to install. A key is
written in one `SET`; its version is an atomic `INCR` beside it.
"""

import threading
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

from sincpro_framework.context.adapters.codecs import PlainCodec
from sincpro_framework.context.domain.codec import ContextCodec
from sincpro_framework.sincpro_logger import logger
from sincpro_framework.store.adapters.entries import InMemoryEntries, KeyValueEntries
from sincpro_framework.store.adapters.in_memory import InMemoryKeyValue
from sincpro_framework.store.domain.key_value import KeyValueStore
from sincpro_framework.store.domain.store import ContextStore, Entry
from sincpro_framework.store.infrastructure.leases import Leases

ENTRY_TTL = timedelta(days=7)
"""How long a draft or a pipeline run lasts after its last keep, unless the store is told
otherwise."""


class InMemoryContexts(ContextStore):
    """A context store in this process's memory — tests and a single worker: lost with the
    process, never seen by a replica. Its entries, leases and key-value section live in the
    same memory, so drafts and pipeline runs work with nothing else set."""

    def __init__(
        self,
        entry_ttl: timedelta = ENTRY_TTL,
        now: Callable[[], datetime] = partial(datetime.now, UTC),
    ) -> None:
        """`now` is the clock an entry's TTL is judged by — a `ManualClock.now` in tests.

            InMemoryContexts()   ->  no context kept; drafts and runs last 7 days from a keep
            InMemoryContexts(timedelta(hours=1), now=clock.now)
                ->  entries an hour, judged by the test's clock

        A context's own `ttl` (`save`) is judged by the wall clock, not by `now`; the leases
        stand on an `InMemoryKeyValue` of the same `now`."""
        self._kept: dict[str, tuple[dict[str, Any], datetime | None]] = {}
        self._versions: dict[str, int] = {}
        self._lock = threading.Lock()
        self._entries = InMemoryEntries(entry_ttl, now)
        self._leases = Leases(InMemoryKeyValue(now))

    def save(self, key: str, values: Mapping[str, Any], ttl: timedelta | None = None) -> None:
        """A copy of `values`, with the moment it expires; the version counts up under a
        lock."""
        until = None if ttl is None else datetime.now(UTC) + ttl
        with self._lock:
            self._kept[key] = (dict(values), until)
            self._versions[key] = self._versions.get(key, 0) + 1

    def load(self, key: str) -> dict[str, Any] | None:
        """A copy of what is kept — an expired key is dropped the moment it is read.

            contexts.save("tenant:acme", {"lang": "es"}, timedelta(minutes=5))
            contexts.load("tenant:acme")   ->  {"lang": "es"}        # a copy: safe to change
            contexts.load("tenant:beta")   ->  None
            ... 5 minutes later            ->  None, the key dropped; its version stays

        Read under the store's lock."""
        with self._lock:
            found = self._kept.get(key)
            if found is None:
                return None
            values, until = found
            if until is not None and until <= datetime.now(UTC):
                del self._kept[key]
                return None
            return dict(values)

    def delete(self, key: str) -> None:
        """The values are dropped; the key's version stays, so a later keep never repeats
        one."""
        with self._lock:
            self._kept.pop(key, None)

    def version(self, key: str) -> int:
        """The keep count held in memory."""
        with self._lock:
            return self._versions.get(key, 0)

    def save_entry(self, key: str, values: Mapping[str, Any], expected: int) -> Entry:
        """Kept by `InMemoryEntries` — the version won under its lock."""
        return self._entries.save(key, values, expected)

    def get_entry(self, key: str) -> Entry | None:
        """Read from `InMemoryEntries`."""
        return self._entries.get(key)

    def delete_entry(self, key: str) -> None:
        """Dropped from `InMemoryEntries`, its version spent."""
        self._entries.delete(key)

    def list_entries(self, folder: str) -> list[str]:
        """Listed by `InMemoryEntries`."""
        return self._entries.listed(folder)

    def leases(self) -> Leases:
        """The leases of this process: held across its threads, not across replicas."""
        return self._leases


class KeyValueContexts(ContextStore):
    """A context store on a `KeyValueStore` — Redis, Valkey, Memcached: every replica and
    every service that sets the same store reads what the others kept. The entries, the leases
    and the caches stand on the same store, each under a section of its own."""

    def __init__(
        self,
        store: KeyValueStore,
        codec: ContextCodec | None = None,
        prefix: str = "sincpro:context",
        entry_ttl: timedelta = ENTRY_TTL,
        now: Callable[[], datetime] = partial(datetime.now, UTC),
    ) -> None:
        """`codec` is how a context is written — plain JSON by default, readable by any language.
        The entries are kept under `{prefix}:entries`, apart from every context a project
        keeps by name; `now` is the clock their TTL is judged by.

            KeyValueContexts(RedisKeyValue(redis))
                ->  contexts at "sincpro:context:<key>", entries under
                    "sincpro:context:entries", leases under "sincpro:context:lease"
            KeyValueContexts(store, TypedCodec(SIATContext), prefix="siat")
                ->  the same under "siat:…", each context read back as its types

        Nothing is written here: the store is first touched by a save or a read."""
        self.store = store
        self.codec = codec or PlainCodec()
        self.prefix = prefix
        self._entries = KeyValueEntries(store, entry_ttl, prefix=f"{prefix}:entries", now=now)
        self._leases = Leases(store, prefix=f"{prefix}:lease")

    def _key(self, key: str) -> str:
        """`"tenant:acme"`  ->  `"sincpro:context:tenant:acme"` — under the prefix."""
        return f"{self.prefix}:{key}"

    def _version_key(self, key: str) -> str:
        """Where a key's version counter lives: beside it, `…:version`."""
        return f"{self._key(key)}:version"

    def save(self, key: str, values: Mapping[str, Any], ttl: timedelta | None = None) -> None:
        """`values` under `key`, its version moved — both gone after `ttl`, so a store that keeps
        a node per execution never grows without end."""
        self.store.set(self._key(key), self.codec.dumps(values), ttl)
        moved = self.store.increment(self._version_key(key))
        if ttl is not None:
            self.store.set(self._version_key(key), str(moved).encode(), ttl)

    def load(self, key: str) -> dict[str, Any] | None:
        """The bytes under the key, read back by the codec — `None` when the store has
        none."""
        data = self.store.get(self._key(key))
        return None if data is None else self.codec.loads(data)

    def delete(self, key: str) -> None:
        """The values and their version counter both gone — a later keep counts from 1
        again."""
        self.store.delete(self._key(key))
        self.store.delete(self._version_key(key))

    def version(self, key: str) -> int:
        """The counter beside the key — `0` when it was never kept, or expired with it."""
        counted = self.store.get(self._version_key(key))
        return 0 if counted is None else int(counted)

    def save_entry(self, key: str, values: Mapping[str, Any], expected: int) -> Entry:
        """Kept by `KeyValueEntries` under `{prefix}:entries` — the version won atomically on
        the shared store."""
        return self._entries.save(key, values, expected)

    def get_entry(self, key: str) -> Entry | None:
        """Read from `KeyValueEntries`."""
        return self._entries.get(key)

    def delete_entry(self, key: str) -> None:
        """Dropped from `KeyValueEntries`, its version spent."""
        self._entries.delete(key)

    def list_entries(self, folder: str) -> list[str]:
        """Listed by `KeyValueEntries`."""
        return self._entries.listed(folder)

    def leases(self) -> Leases:
        """The leases of every replica that shares the store, under `{prefix}:lease`."""
        return self._leases

    def key_value_store(self) -> KeyValueStore:
        """The store every replica shares: what the caches and idempotency records stand
        on."""
        return self.store


_process: InMemoryContexts | None = None
_process_lock = threading.Lock()


def in_memory_contexts() -> InMemoryContexts:
    """Where the drafts and the pipeline runs are kept when no context store is set: this
    process's memory — lost with it and not shared by its replicas. Said once in the logs.

        in_memory_contexts()                          ->  InMemoryContexts(), and the warning
        in_memory_contexts() is in_memory_contexts()  ->  True, no second warning

    Built under a lock, so threads asking at once share the one store."""
    global _process
    with _process_lock:
        if _process is None:
            _process = InMemoryContexts()
            logger.warning(
                "what the context keeps — drafts, pipeline runs, leases, caches, idempotency "
                "records — is kept in this process's memory: keep the context in a store "
                "(store_context_in(...), bus.store_context_in(...)) to share it across restarts and "
                "replicas"
            )
        return _process
