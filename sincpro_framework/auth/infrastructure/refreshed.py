"""What is built from the documents of a scope — a tenant, or what every tenant shares — kept, read
again once `check_every` passed, built again only when a version moved: how a replica holds what
an administrator changes at runtime without reading the store on every call.

    catalogs = Refreshed(read=versions_and_roles_of, build=catalog_of, empty=..., what="roles")
    catalogs.get("bo")      # built once; read again a second later, built again when a version moved
    catalogs.stale("bo")    # the writer's replica: the next get reads at once, the last value kept

Context: each scope reads on its own — one caller reads, the others of that scope wait for it,
every other scope goes on — and never under a lock all scopes share, so a slow store holds back
only the scope being read. A store that does not answer leaves the last value built in effect;
a scope never built is `build(empty)` until the store answers. At most `kept` scopes are held,
the one asked longest ago let go first.
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
    """One scope: the versions it was built from, what was built, when it was last read."""

    versions: tuple[int, ...] | None = None
    value: T | None = None
    checked_at: float | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


class Refreshed[T]:
    """`build(read(scope))` per scope, kept between reads."""

    def __init__(
        self,
        read: Callable[[Any], tuple[tuple[int, ...], Any]],
        build: Callable[[Any], T],
        empty: Any,
        what: str,
        check_every: timedelta = timedelta(seconds=1),
        clock: Callable[[], float] = time.monotonic,
        kept: int = 4096,
    ) -> None:
        """`read(scope)` answers the versions and the content; `build(content)` what is kept;
        `empty` the content of a scope while nothing could be read; `what` names it in a log.
        """
        self.read = read
        self.build = build
        self.empty = empty
        self.what = what
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
        """Whether `held` was read less than `check_every` ago."""
        return held.checked_at is not None and (
            now - held.checked_at < self.check_every.total_seconds()
        )

    def get(self, scope: Hashable) -> T:
        """What `scope` builds to now.

        1. Read less than `check_every` ago: what was built.
        2. Read again — by one caller of the scope; the others wait for it, then use it.
        3. A version moved, or never built: built again.
        4. The store does not answer: what was built, or `build(empty)` the first time.
        Final: what is held for the scope.
        """
        held = self._slot(scope)
        if held.value is not None and self._fresh(held, self.clock()):
            return held.value
        with held.lock:
            now = self.clock()
            if held.value is not None and self._fresh(held, now):
                return held.value
            try:
                versions, content = self.read(scope)
            except StoreUnavailable:
                logger.warning(f"{self.what} could not be read: kept as they were")
                if held.value is None:
                    held.value = self.build(self.empty)
                return held.value
            if held.value is None or held.versions != versions:
                held.value = self.build(content)
                held.versions = versions
            held.checked_at = now
            return held.value

    def stale(self, scope: Hashable | None = None, everything: bool = False) -> None:
        """Read `scope` again on its next `get` — every scope with `everything` — keeping what
        was built while the store does not answer."""
        with self._lock:
            for key, held in self._held.items():
                if everything or key == scope:
                    held.checked_at = None
