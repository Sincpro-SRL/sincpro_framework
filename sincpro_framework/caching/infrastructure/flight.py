"""One caller per key: whoever leads computes, the others serve what there is or wait for it.

Context: across replicas the lead is the store's atomic `add` on a lock key, held for a bounded
time so a caller that dies leading does not hold the key forever; inside one process it is a lock
per key. Either way the others wait a bounded time and then compute themselves — a lost leader
costs one duplicate computation, never a caller that hangs.
"""

import threading
import time
from collections.abc import Callable
from datetime import timedelta

from sincpro_framework.caching.domain.store import KeyValueStore

POLL_SECONDS = 0.005


def awaited[T](read: Callable[[], T | None], within: timedelta) -> T | None:
    """What `read` answers once it answers something, or `None` after `within`."""
    deadline = time.monotonic() + within.total_seconds()
    while time.monotonic() < deadline:
        found = read()
        if found is not None:
            return found
        time.sleep(POLL_SECONDS)
    return None


class StoreFlight:
    def __init__(self, store: KeyValueStore) -> None:
        self.store = store

    def lock_key(self, key: str) -> str:
        return f"{key}:lock"

    def lead(self, key: str, hold: timedelta) -> bool:
        return self.store.add(self.lock_key(key), b"1", hold)

    def release(self, key: str) -> None:
        self.store.delete(self.lock_key(key))


class LocalFlight:
    def __init__(self) -> None:
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def _lock(self, key: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())

    def lead(self, key: str) -> bool:
        return self._lock(key).acquire(blocking=False)

    def release(self, key: str) -> None:
        lock = self._lock(key)
        if lock.locked():
            lock.release()

    def wait(self, key: str, within: timedelta) -> None:
        """Until the leader of `key` is done, or `within` passes."""
        lock = self._lock(key)
        if lock.acquire(timeout=within.total_seconds()):
            lock.release()

    def forget(self, key: str) -> None:
        """Let go of the lock of a key the tier evicted, unless somebody leads it right now.

        Context: one lock per key ever asked would outgrow the bound the tier keeps its values
        in. A lock dropped while a caller is about to take it costs one duplicate computation,
        never a wrong value."""
        with self._guard:
            lock = self._locks.get(key)
            if lock is not None and not lock.locked():
                del self._locks[key]
