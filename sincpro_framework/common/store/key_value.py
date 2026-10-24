"""`KeyValueStore`: the one contract a cache provider implements — six operations every store has,
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
near cache — is built on these by the framework, so an adapter is these six and nothing else,
and it proves itself with `sincpro_framework.runtime.testing.KeyValueStoreContract`. `add`, `increment`
and `take` are atomic: they are what several replicas coordinate on. An abstract class, like
`Repository`.
"""

from abc import ABC, abstractmethod
from collections.abc import Generator
from contextlib import contextmanager
from datetime import timedelta

from sincpro_framework.common.store import exceptions


class KeyValueStore(ABC):
    @contextmanager
    def _reaching(self) -> Generator[None, None, None]:
        """A call to the store's own client: whatever it raises is the store not answering —
        `StoreUnavailable`, carrying the client's error as its cause."""
        try:
            yield
        except exceptions.StoreUnavailable:
            raise
        except Exception as error:
            raise exceptions.StoreUnavailable(
                f"{type(self).__name__} did not answer: {type(error).__name__}: {error}"
            ) from error

    @abstractmethod
    def get_many(self, keys: list[str]) -> list[bytes | None]:
        """The values of `keys`, in that order, `None` for a key that is not there."""

    @abstractmethod
    def set(self, key: str, value: bytes, ttl: timedelta | None = None) -> None:
        """Keep `value` under `key` — for `ttl`, or until deleted."""

    @abstractmethod
    def add(self, key: str, value: bytes, ttl: timedelta | None = None) -> bool:
        """Keep `value` only when `key` is not there, atomically; `True` when it was written."""

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
        return self.get_many([key])[0]
