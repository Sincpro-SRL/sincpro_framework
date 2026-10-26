"""`KeyValueStore`: the one protocol every store engine satisfies — six operations every store has,
and a seventh that compares before it writes.

    Redis / Valkey   MGET · SET PX · SET NX PX · INCR · DEL · GETDEL · WATCH + MULTI
    Memcached        get_multi · set · add · incr · delete · gets + cas · gets + cas
    DynamoDB         BatchGetItem · PutItem · conditional PutItem · UpdateItem ADD · DeleteItem ·
                     DeleteItem ReturnValues=ALL_OLD · conditional PutItem / DeleteItem
    a SQL table      SELECT ANY · upsert · INSERT ON CONFLICT DO NOTHING · UPDATE RETURNING ·
                     DELETE · DELETE RETURNING · UPDATE / DELETE … WHERE value = expected

`replace` is what a lease renews and releases with — "only if it is still mine". It is not
abstract: a store of a project's that has no way to compare and write is still a store, and says
so only when a lease asks it to.

Context: everything clever — keys, tag versions, early expiry, single-flight, idempotency, the
near cache, the context's entries — is built on these by the framework, so an engine is these six
and nothing else, and it proves itself with
`sincpro_framework.runtime.testing.KeyValueStoreContract`. `add`, `increment` and `take` are
atomic: they are what several replicas coordinate on. A `Protocol`, because there are many
engines and no consumer depends on one. An engine subclasses it, so it inherits `get`, `replace`
and `shared`: the framework calls those too, and a class that only matches the six operations
would fail where the cache asks `shared()`.
"""

from abc import abstractmethod
from datetime import timedelta
from typing import Protocol

from sincpro_framework.data_layer.store.domain import exceptions


class KeyValueStore(Protocol):
    """The protocol a key-value engine satisfies: six abstract operations, `replace` when the
    store can compare before it writes, `get` for free, and `shared` — true unless the engine
    lives in this process only.

        class TableKeyValue(KeyValueStore):
            def get_many(self, keys):
                return [self.table.read(key) for key in keys]
            ...
    """

    @abstractmethod
    def get_many(self, keys: list[str]) -> list[bytes | None]:
        """The values of `keys`, in that order, `None` for a key that is not there."""

    @abstractmethod
    def set(self, key: str, value: bytes, ttl: timedelta | None = None) -> None:
        """Store `value` under `key` — for `ttl`, or until deleted."""

    @abstractmethod
    def add(self, key: str, value: bytes, ttl: timedelta | None = None) -> bool:
        """Store `value` only when `key` is not there, atomically; `True` when it was written."""

    @abstractmethod
    def increment(self, key: str) -> int:
        """Add one to the counter under `key`, atomically — a missing counter starts at 0 — and
        answer the new value."""

    @abstractmethod
    def delete(self, key: str) -> None:
        """Remove `key`; a key that is not there is not an error."""

    @abstractmethod
    def take(self, key: str) -> bytes | None:
        """Read `key` and remove it in one step, atomically — of many callers racing for one
        value, exactly one gets it. What a one-time code, a ticket or a claim is spent with.
        """

    def replace(
        self, key: str, expected: bytes, value: bytes | None, ttl: timedelta | None = None
    ) -> bool:
        """Write `value` under `key` — for `ttl`, or until deleted — only when `key` holds
        `expected` now, atomically; `None` deletes it. `True` when it was written: of many
        callers racing with the same `expected`, exactly one is."""
        raise exceptions.ProgrammingError(
            f"{type(self).__name__} cannot compare before it writes (replace): a lease needs a "
            "store that can — InMemoryKeyValue, RedisKeyValue, MemcachedKeyValue, DynamoKeyValue"
        )

    def get(self, key: str) -> bytes | None:
        """The value of one key, `None` when absent — `get_many` of one."""
        return self.get_many([key])[0]

    def shared(self) -> bool:
        """Whether other processes read what is written here — every engine but one in this
        process's memory. A component that stores objects rather than bytes when nothing
        leaves the process (`Cache` behind `bus.cache`) asks this.

            RedisKeyValue(redis).shared()   ->  True
            InMemoryKeyValue().shared()     ->  False
        """
        return True
