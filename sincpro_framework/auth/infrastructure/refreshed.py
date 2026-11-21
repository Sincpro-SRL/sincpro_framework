"""What is built from the documents of a scope — a tenant, or what every tenant shares — kept, read
again once `check_every` passed, built again only when a version moved: how a replica holds what
an administrator changes at runtime without reading the store on every call.

    catalogs = Refreshed(read=versions_and_roles_of, build=catalog_of, unread=code_catalog, ...)
    catalogs.get("bo")      # built once; read again a second later, built again when a version moved
    catalogs.stale("bo")    # the writer's instance: the next get reads, the last value kept

Context: each scope reads on its own — one caller reads, the others of that scope wait for it,
every other scope goes on — never under a lock all scopes share. A store that does not answer
leaves the last value built in effect, and is not asked again for that scope until `check_every`
passes, so an outage never grows into a queue of timeouts. A scope never built while the store
does not answer is `unread()` — or, without one, the store's error, raised: what only restricts
must fail closed. A `stale` waits for a read on its way, so its mark is never lost. At most `kept`
scopes are held, the one asked longest ago let go first.
"""

import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Hashable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sincpro_framework.data_layer.store import StoreUnavailable
from sincpro_framework.sincpro_logger import logger


@dataclass
class _Held[T]:
    """One scope: the versions it was built from, what was built, when it was last asked of the
    store — every change of it under its own lock, so a `stale` mark is never lost."""

    versions: tuple[Hashable, ...] | None = None
    value: T | None = None
    checked_at: float | None = None
    failed_at: float | None = None
    """When the store last did not answer for a scope never built — not asked again before
    `check_every` passes."""
    lock: threading.Lock = field(default_factory=threading.Lock)


class Refreshed[T]:
    """`build(read(scope))` per scope, kept between reads."""

    def __init__(
        self,
        read: Callable[[Any], tuple[tuple[Hashable, ...], Any]],
        build: Callable[[Any], T],
        what: str,
        unread: Callable[[], T] | None = None,
        check_every: timedelta = timedelta(seconds=1),
        clock: Callable[[], float] = time.monotonic,
        kept: int = 4096,
    ) -> None:
        """`read(scope)` answers the versions and the content; `build(content)` what is kept;
        `unread()` what a scope never built is while the store does not answer — `None` raises;
        `what` names it in a log."""
        self.read = read
        self.build = build
        self.what = what
        self.unread = unread
        self.check_every = check_every
        self.clock = clock
        self.kept = kept
        self._lock = threading.Lock()
        self._held: OrderedDict[Hashable, _Held[T]] = OrderedDict()

    def _slot(self, scope: Hashable) -> _Held[T]:
        """The scope's slot, the most recently asked — the oldest let go past `kept`."""
        with self._lock:
            held = self._held.pop(scope, None) or _Held()
            self._held[scope] = held
            while len(self._held) > self.kept:
                self._held.popitem(last=False)
            return held

    def _fresh(self, held: _Held[T], now: float) -> bool:
        """Whether the store was asked about `held` less than `check_every` ago."""
        return held.checked_at is not None and (
            now - held.checked_at < self.check_every.total_seconds()
        )

    def _unbuilt(self, failed: StoreUnavailable) -> T:
        """A scope never built while the store does not answer: `unread()`, or the error."""
        if self.unread is None:
            raise failed
        return self.unread()

    def _unavailable(self, held: _Held[T], failed: StoreUnavailable, now: float) -> T:
        """What a scope is while the store does not answer — the last value, kept and not asked
        again for `check_every`; for one never built, `_unbuilt`, and the store not asked again
        for it either before `check_every` passes."""
        logger.warning(f"{self.what} could not be read: {failed}")
        if held.value is None:
            held.failed_at = now
            return self._unbuilt(failed)
        held.checked_at = now
        return held.value

    def _failed_recently(self, held: _Held[T], now: float) -> bool:
        """Whether the store did not answer for this never-built scope less than
        `check_every` ago."""
        return held.failed_at is not None and (
            now - held.failed_at < self.check_every.total_seconds()
        )

    def get(self, scope: Hashable) -> T:
        """What `scope` builds to now.

        1. Asked of the store less than `check_every` ago: what was built.
        2. Read again — by one caller of the scope; the others wait for it, then use it.
        3. A version moved, or never built: built again.
        4. The store does not answer: as `_unavailable` says; a scope never built is not asked
           again before `check_every` passes.
        Final: what is held for the scope.
        """
        held = self._slot(scope)
        value = held.value
        if value is not None and self._fresh(held, self.clock()):
            return value
        with held.lock:
            now = self.clock()
            if held.value is not None and self._fresh(held, now):
                return held.value
            if held.value is None and self._failed_recently(held, now):
                return self._unbuilt(StoreUnavailable(f"{self.what} could not be read"))
            try:
                versions, content = self.read(scope)
            except StoreUnavailable as failed:
                return self._unavailable(held, failed, now)
            held.failed_at = None
            if held.value is None or held.versions != versions:
                held.value = self.build(content)
                held.versions = versions
            held.checked_at = now
            return held.value

    def stale(self, scope: Hashable | None = None, everything: bool = False) -> None:
        """Read `scope` again on its next `get` — every scope with `everything` — keeping what
        was built while the store does not answer."""
        with self._lock:
            marked = [held for key, held in self._held.items() if everything or key == scope]
        for held in marked:
            with held.lock:
                held.checked_at = None
                held.failed_at = None
