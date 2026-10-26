"""`RedisKeyValue`: the store on Redis or Valkey — the same protocol, one adapter.

    RedisKeyValue(redis.Redis.from_url("redis://cache:6379/0"), prefix="billing:")

The client is yours — its pool, its TLS, its cluster mode — so this is the operations of the port and
nothing else. `prefix` lets several services share one server without sharing keys.
"""

from datetime import timedelta
from typing import Any

from sincpro_framework.data_layer.store.infrastructure.client import ClientKeyValue


def _milliseconds(ttl: timedelta | None) -> int | None:
    """Redis's `PX`: whole milliseconds, at least one — `None`, no ttl, the key never expires."""
    return None if ttl is None else max(1, int(ttl.total_seconds() * 1000))


class RedisKeyValue(ClientKeyValue):
    """The `KeyValueStore` port on redis-py — one command per operation, ttl in `PX`."""

    def __init__(self, client: Any, prefix: str = "") -> None:
        """`client` is a `redis.Redis` — or anything with its `mget/set/incr/delete/getdel`."""
        self.client = client
        self.prefix = prefix

    def get_many(self, keys: list[str]) -> list[bytes | None]:
        """One `MGET` — skipped for no keys, which Redis refuses."""
        with self._reaching():
            if not keys:
                return []
            return list(self.client.mget([self.prefix + key for key in keys]))

    def set(self, key: str, value: bytes, ttl: timedelta | None = None) -> None:
        """`SET … PX`; with no ttl, `SET` alone — which also drops a ttl the key had."""
        with self._reaching():
            self.client.set(self.prefix + key, value, px=_milliseconds(ttl))

    def add(self, key: str, value: bytes, ttl: timedelta | None = None) -> bool:
        """`SET … NX PX` — the write and its expiry in one atomic command."""
        with self._reaching():
            return bool(
                self.client.set(self.prefix + key, value, nx=True, px=_milliseconds(ttl))
            )

    def increment(self, key: str, ttl: timedelta | None = None) -> int:
        """`INCR` — Redis creates a missing counter at 0 itself; a ttl the key had stays. With
        `ttl`, `INCR` and `PEXPIRE` in one `MULTI`: the counter never lives without it."""
        with self._reaching():
            if ttl is None:
                return int(self.client.incr(self.prefix + key))
            both = self.client.pipeline(transaction=True)
            both.incr(self.prefix + key)
            both.pexpire(self.prefix + key, _milliseconds(ttl))
            return int(both.execute()[0])

    def delete(self, key: str) -> None:
        """`DEL`."""
        with self._reaching():

            self.client.delete(self.prefix + key)

    def take(self, key: str) -> bytes | None:
        """`GETDEL` — Redis 6.2 and every Valkey."""
        with self._reaching():
            return self.client.getdel(self.prefix + key)

    def replace(
        self, key: str, expected: bytes, value: bytes | None, ttl: timedelta | None = None
    ) -> bool:
        """`WATCH` the key, compare, then `MULTI` the write: a write by anyone in between aborts
        the transaction, and the answer is `False` — no script on the server.

            store.replace("lease", b"owner-a", b"owner-b", timedelta(seconds=30))  ->  True
            store.replace("lease", b"owner-a", None)   ->  False    # it holds owner-b
            another client writes "lease" between the GET and the EXEC  ->  False

        1. On a pipeline of its own, `WATCH` the prefixed key.
        2. `GET` it: not `expected` (missing included) — `UNWATCH`, `False`.
        3. `MULTI`, then the write: `DEL` for `None`, else `SET … PX`.
        4. `EXEC`: a write by anyone since the `WATCH` aborts it — `WatchError`, `False`.
        Final: `True`, the write applied.
        """
        from redis.exceptions import WatchError

        name = self.prefix + key
        with self._reaching():
            with self.client.pipeline() as pipe:
                try:
                    pipe.watch(name)
                    if pipe.get(name) != expected:
                        pipe.unwatch()
                        return False
                    pipe.multi()
                    if value is None:
                        pipe.delete(name)
                    else:
                        pipe.set(name, value, px=_milliseconds(ttl))
                    pipe.execute()
                except WatchError:
                    return False
            return True
