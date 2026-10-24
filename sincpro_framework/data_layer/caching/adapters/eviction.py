"""The evictions the framework ships: least recently used first, or none.

Context: `Lru` is the default of every `Cache`'s process tier — an unbounded one is a memory
leak. It tracks keys only, never values, in insertion order under one lock.
"""

import threading
from collections import OrderedDict

from sincpro_framework.data_layer.caching.domain import exceptions
from sincpro_framework.data_layer.caching.domain.eviction import Eviction


class Lru(Eviction):
    """Least recently used first: the keys in serving order, the oldest let go past the
    bound."""

    def __init__(self, max_entries: int = 10_000) -> None:
        """`max_entries` is the most keys the tier holds — at least one, else
        `ProgrammingError`.

            Lru()                  ->  holds up to 10 000 keys, none tracked yet
            Lru(max_entries=0)     ->  ProgrammingError("Lru keeps at least one entry: …")

        Only keys are tracked, oldest first, behind one lock shared by every thread."""
        if max_entries < 1:
            raise exceptions.ProgrammingError(
                "Lru keeps at least one entry: max_entries must be >= 1"
            )
        self.max_entries = max_entries
        self._order: OrderedDict[str, None] = OrderedDict()
        self._lock = threading.Lock()

    def touched(self, key: str) -> None:
        """Moves a tracked `key` to the most recent end; an untracked one stays untracked."""
        with self._lock:
            if key in self._order:
                self._order.move_to_end(key)

    def admitted(self, key: str) -> list[str]:
        """Tracks `key` as the most recent and lets go of the oldest past `max_entries`.

            lru = Lru(max_entries=2)
            lru.admitted("a"); lru.admitted("b"); lru.touched("a")
            lru.admitted("c")                                      ->  ["b"]

        1. `key` put, or moved, at the most recent end — so it is never its own victim.
        2. The least recent popped one by one while the tier is over its bound.
        Final: the keys popped, oldest first.
        """
        with self._lock:
            self._order[key] = None
            self._order.move_to_end(key)
            victims: list[str] = []
            while len(self._order) > self.max_entries:
                victim, _ = self._order.popitem(last=False)
                victims.append(victim)
            return victims

    def forgotten(self, key: str) -> None:
        """Stops tracking `key` — a key not tracked is ignored."""
        with self._lock:
            self._order.pop(key, None)


class Unbounded(Eviction):
    """Nothing is ever let go of — for a tier whose keys are known and few."""

    def touched(self, key: str) -> None:
        """Nothing is tracked."""
        return None

    def admitted(self, key: str) -> list[str]:
        """No victim, ever."""
        return []

    def forgotten(self, key: str) -> None:
        """Nothing is tracked."""
        return None
