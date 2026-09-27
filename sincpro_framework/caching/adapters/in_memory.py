"""`InMemoryKeyValue`: the store kept in the process — the default, and what tests use."""

import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sincpro_framework.caching.store import KeyValueStore


def utc_now() -> datetime:
    return datetime.now(UTC)


class InMemoryKeyValue(KeyValueStore):
    def __init__(self, now: Callable[[], datetime] = utc_now) -> None:
        """`now` is the clock expiry is judged by — a `ManualClock.now` in tests."""
        self.now = now
        self._values: dict[str, tuple[bytes, datetime | None]] = {}
        self._lock = threading.Lock()

    def _live(self, key: str) -> bytes | None:
        held = self._values.get(key)
        if held is None:
            return None
        value, expires_at = held
        if expires_at is not None and expires_at <= self.now():
            del self._values[key]
            return None
        return value

    def _expiry(self, ttl: timedelta | None) -> datetime | None:
        return None if ttl is None else self.now() + ttl

    def get_many(self, keys: list[str]) -> list[bytes | None]:
        with self._lock:
            return [self._live(key) for key in keys]

    def set(self, key: str, value: bytes, ttl: timedelta | None = None) -> None:
        with self._lock:
            self._values[key] = (value, self._expiry(ttl))

    def add(self, key: str, value: bytes, ttl: timedelta | None = None) -> bool:
        with self._lock:
            if self._live(key) is not None:
                return False
            self._values[key] = (value, self._expiry(ttl))
            return True

    def increment(self, key: str) -> int:
        with self._lock:
            current = self._live(key)
            counted = (int(current) if current is not None else 0) + 1
            self._values[key] = (str(counted).encode(), None)
            return counted

    def delete(self, key: str) -> None:
        with self._lock:
            self._values.pop(key, None)
