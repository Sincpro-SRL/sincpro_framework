"""`RedisKeyValue`: the store on Redis or Valkey — the same protocol, one adapter.

    RedisKeyValue.from_url("redis://cache:6379/0", prefix="billing:")      a client that fails fast
    RedisKeyValue(redis.Redis.from_url("redis://cache:6379/0"), prefix="billing:")   yours, as built

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

    @classmethod
    def from_url(
        cls,
        url: str,
        prefix: str = "",
        timeout: timedelta = timedelta(milliseconds=500),
        retries: int = 0,
        **options: Any,
    ) -> "RedisKeyValue":
        """A store on the Redis or Valkey at `url`, with a client that fails fast.

            RedisKeyValue.from_url("redis://cache:6379/0")
            RedisKeyValue.from_url(settings.billing.redis_url, prefix="billing:")
            RedisKeyValue.from_url(url, timeout=timedelta(seconds=2), ssl_cert_reqs="required")

        Context: redis-py waits forever by default on a server that stops answering — no socket
        timeout — and retries on top: one request can hang, or try a hundred times. Here both
        the connection and each command give up after `timeout` (half a second), `retries`
        times more at most (none). The framework falls back on its own when the store fails
        (the context goes on in memory, the cache bypasses it), so failing fast is what keeps a
        call fast. Every other option goes to `redis.Redis.from_url` as given, and one of the
        three above given there wins."""
        import redis
        from redis.backoff import NoBackoff
        from redis.retry import Retry

        seconds = timeout.total_seconds()
        settings: dict[str, Any] = {
            "socket_connect_timeout": seconds,
            "socket_timeout": seconds,
            "retry": Retry(NoBackoff(), retries),
            **options,
        }
        return cls(redis.Redis.from_url(url, **settings), prefix)

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

    def touch(self, key: str, ttl: timedelta | None) -> bool:
        """`PEXPIRE` — `True` when the key is there; with no ttl, `PERSIST`, the key checked
        with `EXISTS` in the same `MULTI` (`PERSIST` alone answers `False` for a key that had
        no expiry)."""
        with self._reaching():
            if ttl is not None:
                return bool(self.client.pexpire(self.prefix + key, _milliseconds(ttl)))
            both = self.client.pipeline(transaction=True)
            both.exists(self.prefix + key)
            both.persist(self.prefix + key)
            return bool(both.execute()[0])

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
