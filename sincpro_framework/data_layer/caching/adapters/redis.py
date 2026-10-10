"""`RedisKeyValue`: the store on Redis or Valkey — the same protocol, one adapter.

    RedisKeyValue(redis.Redis.from_url("redis://cache:6379/0"), prefix="billing:")

The client is yours — its pool, its TLS, its cluster mode — so this is the six operations and
nothing else. `prefix` lets several services share one server without sharing keys.
"""

from datetime import timedelta
from typing import Any

from sincpro_framework.common.store import KeyValueStore


def _milliseconds(ttl: timedelta | None) -> int | None:
    return None if ttl is None else max(1, int(ttl.total_seconds() * 1000))


class RedisKeyValue(KeyValueStore):
    def __init__(self, client: Any, prefix: str = "") -> None:
        """`client` is a `redis.Redis` — or anything with its `mget/set/incr/delete/getdel`."""
        self.client = client
        self.prefix = prefix

    def get_many(self, keys: list[str]) -> list[bytes | None]:
        with self._reaching():
            if not keys:
                return []
            return list(self.client.mget([self.prefix + key for key in keys]))

    def set(self, key: str, value: bytes, ttl: timedelta | None = None) -> None:
        with self._reaching():
            self.client.set(self.prefix + key, value, px=_milliseconds(ttl))

    def add(self, key: str, value: bytes, ttl: timedelta | None = None) -> bool:
        with self._reaching():
            return bool(
                self.client.set(self.prefix + key, value, nx=True, px=_milliseconds(ttl))
            )

    def increment(self, key: str) -> int:
        with self._reaching():
            return int(self.client.incr(self.prefix + key))

    def delete(self, key: str) -> None:
        with self._reaching():
            self.client.delete(self.prefix + key)

    def take(self, key: str) -> bytes | None:
        """`GETDEL` — Redis 6.2 and every Valkey."""
        with self._reaching():
            return self.client.getdel(self.prefix + key)
