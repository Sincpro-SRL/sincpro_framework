"""What is built from the documents of a scope — a tenant, or what every tenant shares — cached,
read again once `check_interval` passed, built again only when a version moved: how a replica holds what
an administrator changes at runtime without reading the store on every call.

    catalogs = ScopedCache(read=versions_and_roles_of, build=catalog_of, fallback=code_catalog, ...)
    catalogs.get("bo")      # built once; read again a second later, built again when a version moved
    catalogs.invalidate("bo")    # the writer's instance: the next get reads, the last value retained

Context: each scope reads on its own — one caller reads, the others of that scope wait for it,
every other scope goes on — never under a lock all scopes share. A store that does not answer
leaves the last value built in effect, and is not asked again for that scope until `check_interval`
passes, so an outage never grows into a queue of timeouts. A scope never built while the store
does not answer is `fallback()` — or, without one, the store's error, raised: what only restricts
must fail closed. An `invalidate` waits for a read on its way, so its mark is never lost. At most `max_entries`
scopes are cached, the one asked longest ago evicted first.
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
class _Entry[T]:
    """One scope: the versions it was built from, what was built, when it was last asked of the
    store — every change of it under its own lock, so a `stale` mark is never lost."""

    versions: tuple[Hashable, ...] | None = None
    value: T | None = None
    checked_at: float | None = None
    failed_at: float | None = None
    """When the store last did not answer for a scope never built — not asked again before
    `check_interval` passes."""
    lock: threading.Lock = field(default_factory=threading.Lock)


class ScopedCache[T]:
    """`build(read(scope))` per scope, cached between reads."""

    def __init__(
        self,
        read: Callable[[Any], tuple[tuple[Hashable, ...], Any]],
        build: Callable[[Any], T],
        what: str,
        fallback: Callable[[], T] | None = None,
        check_interval: timedelta = timedelta(seconds=1),
        clock: Callable[[], float] = time.monotonic,
        max_entries: int = 4096,
    ) -> None:
        """`read(scope)` answers the versions and the content; `build(content)` what is cached;
        `fallback()` what a scope never built is while the store does not answer — `None` raises;
        `what` names it in a log; `max_entries` bounds how many scopes are cached."""
        self.read = read
        self.build = build
        self.what = what
        self.fallback = fallback
        self.check_interval = check_interval
        self.clock = clock
        self.max_entries = max_entries
        self._lock = threading.Lock()
        self._entries: OrderedDict[Hashable, _Entry[T]] = OrderedDict()

    def _entry(self, scope: Hashable) -> _Entry[T]:
        """The scope's entry, the most recently asked — the oldest evicted past `max_entries`."""
        with self._lock:
            held = self._entries.pop(scope, None) or _Entry()
            self._entries[scope] = held
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
            return held

    def _fresh(self, held: _Entry[T], now: float) -> bool:
        """Whether the store was asked about `held` less than `check_interval` ago."""
        return held.checked_at is not None and (
            now - held.checked_at < self.check_interval.total_seconds()
        )

    def _unbuilt(self, failed: StoreUnavailable) -> T:
        """A scope never built while the store does not answer: `fallback()`, or the error."""
        if self.fallback is None:
            raise failed
        return self.fallback()

    def _unavailable(self, held: _Entry[T], failed: StoreUnavailable, now: float) -> T:
        """What a scope is while the store does not answer — the last value, retained and not asked
        again for `check_interval`; for one never built, `_unbuilt`, and the store not asked again
        for it either before `check_interval` passes."""
        logger.warning(f"{self.what} could not be read: {failed}")
        if held.value is None:
            held.failed_at = now
            return self._unbuilt(failed)
        held.checked_at = now
        return held.value

    def _failed_recently(self, held: _Entry[T], now: float) -> bool:
        """Whether the store did not answer for this never-built scope less than
        `check_interval` ago."""
        return held.failed_at is not None and (
            now - held.failed_at < self.check_interval.total_seconds()
        )

    def get(self, scope: Hashable) -> T:
        """What `scope` builds to now.

        1. Asked of the store less than `check_interval` ago: what was built.
        2. Read again — by one caller of the scope; the others wait for it, then use it.
        3. A version moved, or never built: built again.
        4. The store does not answer: as `_unavailable` says; a scope never built is not asked
           again before `check_interval` passes.
        Final: what is held for the scope.
        """
        held = self._entry(scope)
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

    def invalidate(self, scope: Hashable | None = None, everything: bool = False) -> None:
        """Read `scope` again on its next `get` — every scope with `everything` — retaining what
        was built while the store does not answer."""
        with self._lock:
            marked = [
                held for key, held in self._entries.items() if everything or key == scope
            ]
        for held in marked:
            with held.lock:
                held.checked_at = None
                held.failed_at = None
