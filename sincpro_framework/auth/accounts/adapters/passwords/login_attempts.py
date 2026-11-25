"""`LoginAttempts`: an account locked after too many failed attempts in a row — tried one attempt
at a time, its failures counted on the store every replica shares, the same for a username
nobody holds.

    attempts = LoginAttempts(max_failures=5, window=timedelta(minutes=15),
                             lock_for=timedelta(minutes=15))
    with attempts.attempt(username):        RateLimited (429, Retry-After) while locked
        verify the password                 Unauthenticated: a failure; ending well: the count starts over
    attempts.locked_for(username)           ->  how long it stays locked, for a screen

Context: attempts at one account take turns — on every replica, through a short claim on the
store — so its failures are counted exactly: fifty guesses sent at once are checked one after
the other, and once `max_failures` of them failed, the rest find the account locked. A count
taken while attempts ran side by side would let all fifty be checked before the first failure
was counted. Only `Unauthenticated` from the block is a failure: the project's database down is
not a guess. Keys are the digest of the username, so a username — often an email — never lands
in the store, and an unknown one is counted and locked like a real one: nobody learns which
exist. A store that does not answer lets the attempt through — logged, the store left alone for
a while — so a Redis restart never locks everyone out; the password is still checked.
"""

import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import timedelta

from sincpro_framework.auth.accounts.domain.events import AccountLocked
from sincpro_framework.auth.domain import exceptions
from sincpro_framework.auth.domain.events import SUBJECT, EventPublisher
from sincpro_framework.auth.infrastructure.announcing import announce
from sincpro_framework.auth.infrastructure.digests import digest
from sincpro_framework.context.entrypoint.resolution import current_store, key_value_store_of
from sincpro_framework.data_layer.store import KeyValueStore, StoreBypass, StoreUnavailable
from sincpro_framework.rate_limiter import RateLimited
from sincpro_framework.sincpro_logger import logger

TURN_LASTS = timedelta(seconds=30)
"""How long an attempt holds its account's turn at most — a replica that dies mid-attempt
frees it then."""
TURN_WAIT = timedelta(seconds=5)
"""How long an attempt waits for the one before it on the same account."""


class LoginAttempts:
    """Attempts per key, one at a time, and the lock too many failures lead to."""

    def __init__(
        self,
        store: KeyValueStore | None = None,
        max_failures: int = 5,
        window: timedelta = timedelta(minutes=15),
        lock_for: timedelta = timedelta(minutes=15),
        prefix: str = "sincpro:auth:login",
        clock: Callable[[], float] = time.time,
        publisher: EventPublisher | None = None,
    ) -> None:
        """`window` is how long failures are counted from the last one; `lock_for` how long the
        lock lasts once `max_failures` is reached; `publisher` is told every lock
        (`AccountLocked`)."""
        self._store = store
        self.max_failures = max_failures
        self.window = window
        self.lock_for = lock_for
        self.prefix = prefix
        self.clock = clock
        self._bypass = StoreBypass(timedelta(seconds=30), clock)
        self.publisher = publisher

    def store(self) -> KeyValueStore:
        """The store the attempts are counted in — the one given, else the running context's."""
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

    def _turn_taken(self, key: str) -> bool:
        """The turn of `key` claimed — waiting up to `TURN_WAIT` for the attempt holding it;
        `False` when the store does not answer: the attempt goes on unclaimed. `RateLimited`
        for a second when the wait runs out."""
        if self._bypass.is_open():
            return False
        until = time.monotonic() + TURN_WAIT.total_seconds()
        try:
            while not self.store().add(self._key("turn", key), b"1", TURN_LASTS):
                if time.monotonic() >= until:
                    raise RateLimited("login", timedelta(seconds=1))
                time.sleep(0.05)
        except StoreUnavailable:
            self._unavailable()
            return False
        return True

    def _turn_given_back(self, key: str) -> None:
        try:
            self.store().delete(self._key("turn", key))
        except StoreUnavailable:
            self._unavailable()

    def _failed(self, key: str) -> None:
        """One failure counted — atomically —; the one that reaches `max_failures` locks `key`
        for `lock_for`, its count started over, and is announced."""
        if self._bypass.is_open():
            return
        try:
            store = self.store()
            if store.increment(self._key("failures", key), self.window) < self.max_failures:
                return
            until = self.clock() + self.lock_for.total_seconds()
            store.set(self._key("lock", key), str(until).encode(), self.lock_for)
            store.delete(self._key("failures", key))
        except StoreUnavailable:
            self._unavailable()
            return
        announce(
            self.publisher,
            AccountLocked(
                entity_type=SUBJECT,
                entity_id=key,
                locked_seconds=int(self.lock_for.total_seconds()),
            ),
        )

    def _forgotten(self, key: str) -> None:
        """The count of `key` started over."""
        if self._bypass.is_open():
            return
        try:
            self.store().delete(self._key("failures", key))
        except StoreUnavailable:
            self._unavailable()

    @contextmanager
    def attempt(self, key: str) -> Generator[None, None, None]:
        """One attempt at `key`, in its turn: the block raising `Unauthenticated` is a failure,
        ending well starts the count over.

            with attempts.attempt(dto.username):
                if not hasher.verify(stored, dto.password):
                    raise Unauthenticated("wrong username or password")

        1. The account's turn, waited for while another attempt at it runs — on any replica.
        2. Locked: `RateLimited` — 429, `Retry-After` — and the block never runs.
        3. The block raises `Unauthenticated`: one failure; the one that reaches
           `max_failures` locks `key` for `lock_for`. Anything else it raises counts nothing.
        Final: the block ended well — the count starts over. The turn is given back however
        the block ended.
        """
        claimed = self._turn_taken(key)
        try:
            left = self.locked_for(key)
            if left is not None:
                raise RateLimited("login", left)
            try:
                yield
            except exceptions.Unauthenticated:
                self._failed(key)
                raise
            self._forgotten(key)
        finally:
            if claimed:
                self._turn_given_back(key)
