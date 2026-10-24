"""`InMemoryKeyValue`: the store kept in the process — the default, and what tests use."""

import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import partial

from sincpro_framework.store.domain.key_value import KeyValueStore


class InMemoryKeyValue(KeyValueStore):
    """A dict of `key → (value, expires_at)` behind one lock — atomic within the process, and
    nothing beyond it: two replicas each have their own.

        store = InMemoryKeyValue(now=clock.now)
        store.add("lock:42", b"me", ttl=timedelta(seconds=30))   ->  True
        store.add("lock:42", b"you")                             ->  False   still live

    An expired value is dropped the first time it is read, never by a sweeper.
    """

    def __init__(self, now: Callable[[], datetime] = partial(datetime.now, UTC)) -> None:
        """`now` is the clock expiry is judged by — a `ManualClock.now` in tests."""
        self.now = now
        self._values: dict[str, tuple[bytes, datetime | None]] = {}
        self._lock = threading.Lock()

    def _live(self, key: str) -> bytes | None:
        """The value under `key` if it has not expired — an expired one is deleted on the
        spot. Called with the lock held."""
        held = self._values.get(key)
        if held is None:
            return None
        value, expires_at = held
        if expires_at is not None and expires_at <= self.now():
            del self._values[key]
            return None
        return value

    def _expiry(self, ttl: timedelta | None) -> datetime | None:
        """When a value written now with `ttl` expires; `None` keeps it until deleted."""
        return None if ttl is None else self.now() + ttl

    def get_many(self, keys: list[str]) -> list[bytes | None]:
        """Every key read under one hold of the lock, expired ones as `None`."""
        with self._lock:
            return [self._live(key) for key in keys]

    def set(self, key: str, value: bytes, ttl: timedelta | None = None) -> None:
        """Overwrites whatever was there, with a fresh expiry."""
        with self._lock:
            self._values[key] = (value, self._expiry(ttl))

    def add(self, key: str, value: bytes, ttl: timedelta | None = None) -> bool:
        """Writes only when no live value is there — an expired one counts as absent.

            store.add("lock:42", b"me", timedelta(seconds=30))   ->  True
            store.add("lock:42", b"you")                         ->  False    # still live
            ... 31 seconds later: store.add("lock:42", b"you")   ->  True

        The read and the write happen under one hold of the lock: of two racing, one wins."""
        with self._lock:
            if self._live(key) is not None:
                return False
            self._values[key] = (value, self._expiry(ttl))
            return True

    def increment(self, key: str) -> int:
        """The counter kept as its decimal text, like Redis; the incremented value is kept
        with no expiry, whatever the key had.

            store.increment("failures")   ->  1          # kept as b"1"
            store.increment("failures")   ->  2
            store.set("n", b"x"); store.increment("n")   ->  ValueError: not a number

        An expired value counts as absent: the count starts again at 1."""
        with self._lock:
            current = self._live(key)
            counted = (int(current) if current is not None else 0) + 1
            self._values[key] = (str(counted).encode(), None)
            return counted

    def delete(self, key: str) -> None:
        """Drops the key, live or expired."""
        with self._lock:
            self._values.pop(key, None)

    def take(self, key: str) -> bytes | None:
        """Reads and drops under one hold of the lock; an expired value answers `None`."""
        with self._lock:
            value = self._live(key)
            self._values.pop(key, None)
            return value

    def replace(
        self, key: str, expected: bytes, value: bytes | None, ttl: timedelta | None = None
    ) -> bool:
        """Compares and writes under one hold of the lock; an expired value never matches.

            store.replace("lease", b"owner-a", b"owner-b", timedelta(seconds=30))  ->  True
            store.replace("lease", b"owner-a", None)   ->  False   # it holds owner-b now
            store.replace("lease", b"owner-b", None)   ->  True, the key dropped

        A new value takes a fresh expiry from `ttl` — `None`, kept until deleted."""
        with self._lock:
            if self._live(key) != expected:
                return False
            if value is None:
                self._values.pop(key, None)
            else:
                self._values[key] = (value, self._expiry(ttl))
            return True
