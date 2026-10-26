"""`InMemoryKeyValue`: the store in the process memory — the default, and what tests use."""

import threading
from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import partial

from sincpro_framework.data_layer.store.domain.key_value import KeyValueStore

SWEEP_EVERY = 1000
"""How many writes go by between two sweeps of what expired and was never read again."""


class InMemoryKeyValue(KeyValueStore):
    """A dict of `key → (value, expires_at)` behind one lock — atomic within the process, and
    nothing beyond it: two replicas each have their own.

        store = InMemoryKeyValue(now=clock.now)
        store.add("lock:42", b"me", ttl=timedelta(seconds=30))   ->  True
        store.add("lock:42", b"you")                             ->  False   still live
        InMemoryKeyValue(max_entries=50_000)                     ->  the least recently used
                                                                     let go past 50 000

    An expired value is dropped the first time it is read, and every `SWEEP_EVERY` writes the
    ones nobody read again are swept: what is written and never read does not stay forever.
    """

    def __init__(
        self,
        now: Callable[[], datetime] = partial(datetime.now, UTC),
        max_entries: int | None = None,
    ) -> None:
        """`now` is the clock expiry is judged by — a `ManualClock.now` in tests;
        `max_entries` bounds how many keys it holds — the least recently read or written let
        go first, the expired before them — unbounded when `None`."""
        self.now = now
        self.max_entries = max_entries
        self._values: OrderedDict[str, tuple[bytes, datetime | None]] = OrderedDict()
        self._lock = threading.Lock()
        self._writes = 0

    def _live(self, key: str) -> bytes | None:
        """The value under `key` if it has not expired — an expired one is deleted on the
        spot, a live one made the most recently used. Called with the lock held."""
        held = self._values.get(key)
        if held is None:
            return None
        value, expires_at = held
        if expires_at is not None and expires_at <= self.now():
            del self._values[key]
            return None
        self._values.move_to_end(key)
        return value

    def _swept(self) -> None:
        """Every expired value dropped. Called with the lock held."""
        now = self.now()
        for key in [
            key
            for key, (_, until) in self._values.items()
            if until is not None and until <= now
        ]:
            del self._values[key]

    def _written(self, key: str, value: bytes, expires_at: datetime | None) -> None:
        """`value` stored under `key` as the most recently used — and the store kept within its
        bounds. Called with the lock held.

        1. Stored, at the most recent end.
        2. Every `SWEEP_EVERY` writes, the expired swept.
        Final: past `max_entries`, the expired swept, then the least recently used let go.
        """
        self._values[key] = (value, expires_at)
        self._values.move_to_end(key)
        self._writes += 1
        if self._writes % SWEEP_EVERY == 0:
            self._swept()
        if self.max_entries is not None and len(self._values) > self.max_entries:
            self._swept()
            while len(self._values) > self.max_entries:
                self._values.popitem(last=False)

    def _expiry(self, ttl: timedelta | None) -> datetime | None:
        """When a value written now with `ttl` expires; `None` lives until deleted."""
        return None if ttl is None else self.now() + ttl

    def get_many(self, keys: list[str]) -> list[bytes | None]:
        """Every key read under one hold of the lock, expired ones as `None`."""
        with self._lock:
            return [self._live(key) for key in keys]

    def set(self, key: str, value: bytes, ttl: timedelta | None = None) -> None:
        """Overwrites whatever was there, with a fresh expiry."""
        with self._lock:
            self._written(key, value, self._expiry(ttl))

    def add(self, key: str, value: bytes, ttl: timedelta | None = None) -> bool:
        """Writes only when no live value is there — an expired one counts as absent.

            store.add("lock:42", b"me", timedelta(seconds=30))   ->  True
            store.add("lock:42", b"you")                         ->  False    # still live
            ... 31 seconds later: store.add("lock:42", b"you")   ->  True

        The read and the write happen under one hold of the lock: of two racing, one wins."""
        with self._lock:
            if self._live(key) is not None:
                return False
            self._written(key, value, self._expiry(ttl))
            return True

    def increment(self, key: str, ttl: timedelta | None = None) -> int:
        """The counter stored as its decimal text, like Redis — with `ttl`, a fresh expiry;
        without, the one the key had.

            store.increment("failures")   ->  1          # stored as b"1"
            store.increment("failures")   ->  2
            store.increment("v", timedelta(seconds=5))   ->  1, gone in 5 seconds
            store.set("n", b"x"); store.increment("n")   ->  ValueError: not a number

        An expired value counts as absent: the count starts again at 1."""
        with self._lock:
            current = self._live(key)
            counted = (int(current) if current is not None else 0) + 1
            kept = self._values[key][1] if current is not None else None
            self._written(key, str(counted).encode(), self._expiry(ttl) if ttl else kept)
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

        A new value takes a fresh expiry from `ttl` — `None`, stored until deleted."""
        with self._lock:
            if self._live(key) != expected:
                return False
            if value is None:
                self._values.pop(key, None)
            else:
                self._written(key, value, self._expiry(ttl))
            return True

    def touch(self, key: str, ttl: timedelta | None) -> bool:
        """A live value given a fresh expiry under one hold of the lock — `False` when there
        is none."""
        with self._lock:
            value = self._live(key)
            if value is None:
                return False
            self._values[key] = (value, self._expiry(ttl))
            return True

    def shared(self) -> bool:
        """Never: what is written here lives in this process only."""
        return False
