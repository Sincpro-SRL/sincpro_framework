"""`MemcachedKeyValue`: the store on Memcached, through pymemcache.

    MemcachedKeyValue(pymemcache.Client("cache:11211"), prefix="billing:")

Context: Memcached expires in whole seconds, so a ttl under one second is one second; its
`incr` does not create a missing counter, so `increment` creates it with `add` and, when another
client created it first, increments that one; and it has no read-and-delete, so `take` reads with
`gets` and spends the value with `cas` — the one caller whose `cas` wins took it.
"""

import math
from datetime import timedelta
from typing import Any

from sincpro_framework.common.store import KeyValueStore


def _seconds(ttl: timedelta | None) -> int:
    return 0 if ttl is None else max(1, math.ceil(ttl.total_seconds()))


class MemcachedKeyValue(KeyValueStore):
    def __init__(self, client: Any, prefix: str = "") -> None:
        """`client` is a `pymemcache.Client` — or anything with its
        `get_many/set/add/incr/delete/gets/cas`."""
        self.client = client
        self.prefix = prefix

    def get_many(self, keys: list[str]) -> list[bytes | None]:
        found = self.client.get_many([self.prefix + key for key in keys])
        return [found.get(self.prefix + key) for key in keys]

    def set(self, key: str, value: bytes, ttl: timedelta | None = None) -> None:
        self.client.set(self.prefix + key, value, expire=_seconds(ttl), noreply=False)

    def add(self, key: str, value: bytes, ttl: timedelta | None = None) -> bool:
        return bool(
            self.client.add(self.prefix + key, value, expire=_seconds(ttl), noreply=False)
        )

    def increment(self, key: str) -> int:
        while True:
            counted = self.client.incr(self.prefix + key, 1, noreply=False)
            if counted is not None:
                return int(counted)
            if self.client.add(self.prefix + key, b"1", expire=0, noreply=False):
                return 1

    def delete(self, key: str) -> None:
        self.client.delete(self.prefix + key, noreply=False)

    def take(self, key: str) -> bytes | None:
        value, token = self.client.gets(self.prefix + key)
        if value is None:
            return None
        if not self.client.cas(self.prefix + key, b"", token, expire=1, noreply=False):
            return None
        self.client.delete(self.prefix + key, noreply=False)
        return value
