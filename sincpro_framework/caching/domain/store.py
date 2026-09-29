"""`KeyValueStore`: the one contract a cache provider implements — six operations every store has.

    Redis / Valkey   MGET · SET PX · SET NX PX · INCR · DEL · GETDEL
    Memcached        get_multi · set · add · incr · delete · gets + cas
    DynamoDB         BatchGetItem · PutItem · conditional PutItem · UpdateItem ADD · DeleteItem ·
                     DeleteItem ReturnValues=ALL_OLD
    a SQL table      SELECT ANY · upsert · INSERT ON CONFLICT DO NOTHING · UPDATE RETURNING ·
                     DELETE · DELETE RETURNING

Context: everything clever — keys, tag versions, early expiry, single-flight, idempotency, the
near cache — is built on these by the framework, so an adapter is these six and nothing else,
and it proves itself with `sincpro_framework.testing.KeyValueStoreContract`. `add`, `increment`
and `take` are atomic: they are what several replicas coordinate on. An abstract class, like
`Repository`.
"""

from abc import ABC, abstractmethod
from datetime import timedelta


class KeyValueStore(ABC):
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

    def get(self, key: str) -> bytes | None:
        return self.get_many([key])[0]
