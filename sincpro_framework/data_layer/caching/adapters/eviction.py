"""The evictions the framework ships: least recently used first, or none.

Context: `Lru` is the default of every `Cache`'s process tier — an unbounded one is a memory
leak. It tracks keys only, never values, in insertion order under one lock.
"""

import threading
from collections import OrderedDict

from sincpro_framework.data_layer.caching.domain.eviction import Eviction
from sincpro_framework.ddd.exceptions import ContractViolation


class Lru(Eviction):
    def __init__(self, max_entries: int = 10_000) -> None:
        if max_entries < 1:
            raise ContractViolation("Lru keeps at least one entry: max_entries must be >= 1")
        self.max_entries = max_entries
        self._order: OrderedDict[str, None] = OrderedDict()
        self._lock = threading.Lock()

    def touched(self, key: str) -> None:
        with self._lock:
            if key in self._order:
                self._order.move_to_end(key)

    def admitted(self, key: str) -> list[str]:
        with self._lock:
            self._order[key] = None
            self._order.move_to_end(key)
            victims: list[str] = []
            while len(self._order) > self.max_entries:
                victim, _ = self._order.popitem(last=False)
                victims.append(victim)
            return victims

    def forgotten(self, key: str) -> None:
        with self._lock:
            self._order.pop(key, None)


class Unbounded(Eviction):
    """Nothing is ever let go of — for a tier whose keys are known and few."""

    def touched(self, key: str) -> None:
        return None

    def admitted(self, key: str) -> list[str]:
        return []

    def forgotten(self, key: str) -> None:
        return None
