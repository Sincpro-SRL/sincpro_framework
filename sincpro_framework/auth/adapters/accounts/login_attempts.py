"""`LoginAttempts`: an account locked after too many failed attempts in a row — counted
atomically, on the store every replica shares, the same for a username nobody holds.

    attempts = LoginAttempts(max_failures=5, window=timedelta(minutes=15),
                             lock_for=timedelta(minutes=15))
    attempts.refuse_if_locked(username)     RateLimited (429, Retry-After) while locked
    attempts.failed(username)               ->  timedelta(minutes=15) when this one locked it
    attempts.succeeded(username)            the count starts over

Context: the count is an atomic increment — two failures at once are two — never a counter read,
changed and saved, which loses failures under concurrency (quipu's `Cuenta`). Keys are the
digest of the username, so a username — often an email — never lands in the store, and an
unknown one is counted and locked like a real one: nobody learns which exist. A store that does
not answer lets the attempt through — logged, the store left alone for a while — so a Redis
restart never locks everyone out; the password is still checked.
"""

import time
from collections.abc import Callable
from datetime import timedelta

from sincpro_framework.auth.infrastructure.digests import digest
from sincpro_framework.context.entrypoint.resolution import current_store, key_value_store_of
from sincpro_framework.data_layer.store import KeyValueStore, StoreBypass, StoreUnavailable
from sincpro_framework.rate_limiter import RateLimited
from sincpro_framework.sincpro_logger import logger


class LoginAttempts:
    """Failed attempts per key, and the lock they lead to."""

    def __init__(
        self,
        store: KeyValueStore | None = None,
        max_failures: int = 5,
        window: timedelta = timedelta(minutes=15),
        lock_for: timedelta = timedelta(minutes=15),
        prefix: str = "sincpro:auth:login",
        clock: Callable[[], float] = time.time,
    ) -> None:
        """`window` is how long failures are counted from the last one; `lock_for` how long the
        lock lasts once `max_failures` is reached."""
        self._store = store
        self.max_failures = max_failures
        self.window = window
        self.lock_for = lock_for
        self.prefix = prefix
        self.clock = clock
        self._bypass = StoreBypass(timedelta(seconds=30), clock)

    def store(self) -> KeyValueStore:
        """The store the failures are counted kept in — the one given, else the running
        context's."""
        return self._store if self._store is not None else key_value_store_of(current_store())

    def _key(self, kind: str, key: str) -> str:
        return f"{self.prefix}:{kind}:{digest(key.strip().casefold())}"

    def _unavailable(self) -> None:
        logger.warning("login attempts store unavailable: attempts not counted")
        self._bypass.trip()

    def locked_for(self, key: str) -> timedelta | None:
        """How long `key` stays locked — `None` when it is not."""
        if self._bypass.is_open():
            return None
        try:
            until = self.store().get(self._key("lock", key))
        except StoreUnavailable:
            self._unavailable()
            return None
        if until is None:
            return None
        left = float(until) - self.clock()
        return timedelta(seconds=left) if left > 0 else None

    def refuse_if_locked(self, key: str) -> None:
        """`RateLimited` — 429, `Retry-After` — while `key` is locked."""
        left = self.locked_for(key)
        if left is not None:
            raise RateLimited("login", left)

    def failed(self, key: str) -> timedelta | None:
        """Count one failure — atomically. The one that reaches `max_failures` locks `key` for
        `lock_for`, and that is answered; `None` otherwise."""
        if self._bypass.is_open():
            return None
        try:
            store = self.store()
            count = store.increment(self._key("failures", key), self.window)
            if count < self.max_failures:
                return None
            until = self.clock() + self.lock_for.total_seconds()
            store.set(self._key("lock", key), str(until).encode(), self.lock_for)
            store.delete(self._key("failures", key))
            return self.lock_for
        except StoreUnavailable:
            self._unavailable()
            return None

    def succeeded(self, key: str) -> None:
        """The count of `key` starts over."""
        try:
            self.store().delete(self._key("failures", key))
        except StoreUnavailable:
            self._unavailable()
