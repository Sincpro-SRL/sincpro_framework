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

from sincpro_framework.data_layer.store.infrastructure.client import ClientKeyValue


def _seconds(ttl: timedelta | None) -> int:
    """Memcached's `expire`: seconds rounded up, at least one — 0 (no ttl) never expires.

        _seconds(timedelta(milliseconds=200))  ->  1
        _seconds(None)                         ->  0

    Memcached takes no fraction of a second.
    """
    return 0 if ttl is None else max(1, math.ceil(ttl.total_seconds()))


class MemcachedKeyValue(ClientKeyValue):
    """The `KeyValueStore` port on pymemcache, every write acknowledged (`noreply=False`)."""

    def __init__(self, client: Any, prefix: str = "") -> None:
        """`client` is a `pymemcache.Client` — or anything with its
        `get_many/set/add/incr/delete/gets/cas`."""
        self.client = client
        self.prefix = prefix

    def get_many(self, keys: list[str]) -> list[bytes | None]:
        """One `get_many`, answered back in the order of `keys` — Memcached answers a dict."""
        with self._reaching():
            found = self.client.get_many([self.prefix + key for key in keys])
            return [found.get(self.prefix + key) for key in keys]

    def set(self, key: str, value: bytes, ttl: timedelta | None = None) -> None:
        """`set`, the ttl in whole seconds rounded up."""
        with self._reaching():
            self.client.set(self.prefix + key, value, expire=_seconds(ttl), noreply=False)

    def add(self, key: str, value: bytes, ttl: timedelta | None = None) -> bool:
        """Memcached's own `add` — atomic on the server."""
        with self._reaching():
            return bool(
                self.client.add(self.prefix + key, value, expire=_seconds(ttl), noreply=False)
            )

    def increment(self, key: str, ttl: timedelta | None = None) -> int:
        """`incr`, and an `add` of `1` for a counter that is not there yet.

        1. `incr` by one — Memcached answers `None` for a missing key instead of creating it.
           1.1 With `ttl`, a `touch` renews its expiry right after — Memcached has no command
               that does both; the counter it touches already had an expiry of its own.
        2. Missing: `add` writes `1`, with `ttl` (none when unsaid), and answers 1.
           2.1 Another client created it in between: `add` loses, back to step 1.
        Final: the counter after this caller's addition.

            store.increment("failures")   ->  1     # incr None, add b"1" won
            store.increment("failures")   ->  2     # incr
            two clients on a missing key  ->  1 and 2, one `add` lost and went round again
        """
        with self._reaching():
            while True:
                counted = self.client.incr(self.prefix + key, 1, noreply=False)
                if counted is not None:
                    if ttl is not None:
                        self.client.touch(self.prefix + key, _seconds(ttl), noreply=False)
                    return int(counted)
                if self.client.add(
                    self.prefix + key, b"1", expire=_seconds(ttl), noreply=False
                ):
                    return 1

    def delete(self, key: str) -> None:
        """Memcached's `delete` — a missing key answers false, never raises."""
        with self._reaching():
            self.client.delete(self.prefix + key, noreply=False)

    def take(self, key: str) -> bytes | None:
        """`gets` then `cas` to an empty value: the one caller whose `cas` wins took it.

        1. `gets` the value with its cas token — `None` when it is not there.
        2. `cas` it to `b""` for one second; a caller that lost the race answers `None`.
        3. The winner deletes the emptied key.
        Final: the value the winner read.

            store.take("claim:42")   ->  b"owner-a"     # this caller's cas won
            store.take("claim:42")   ->  None           # gone, or another caller's cas won

        Between the winning `cas` and the delete the key holds `b""` — a reader there sees it.
        """
        with self._reaching():

            value, token = self.client.gets(self.prefix + key)
            if value is None:
                return None
            if not self.client.cas(self.prefix + key, b"", token, expire=1, noreply=False):
                return None
            self.client.delete(self.prefix + key, noreply=False)
            return value

    def replace(
        self, key: str, expected: bytes, value: bytes | None, ttl: timedelta | None = None
    ) -> bool:
        """`gets` then `cas`: a write by anyone in between makes the `cas` fail. A delete goes
        through the empty value `take` spends with, so only the one whose `cas` won deletes.

            store.replace("lease", b"owner-a", b"owner-b", timedelta(seconds=30))  ->  True
            store.replace("lease", b"owner-a", None)   ->  False    # it holds owner-b
            two callers replacing the same value       ->  one `cas` wins: True and False

        A ttl under a second is one second — Memcached expires in whole seconds.
        """
        with self._reaching():
            held, token = self.client.gets(self.prefix + key)
            if held != expected:
                return False
            if value is not None:
                return bool(
                    self.client.cas(
                        self.prefix + key, value, token, expire=_seconds(ttl), noreply=False
                    )
                )
            if not self.client.cas(self.prefix + key, b"", token, expire=1, noreply=False):
                return False
            self.client.delete(self.prefix + key, noreply=False)
            return True
