"""`Eviction`: how the process tier of a `Cache` stays bounded — the port.

    touched(key)       a kept value was served
    admitted(key)      a value was kept: the keys to let go of now
    forgotten(key)     a value was let go of by somebody else

Context: the process tier keeps objects in this process's memory; with no bound, every distinct
key ever asked for stays — a leak that shows as a pod killed for memory days after a deploy.
`Lru(max_entries=10_000)` is the default and `Unbounded()` is for a tier whose keys are known and
few (`adapters/eviction.py`). An eviction is called from every thread that uses the cache, so it
is thread-safe, and after `admitted` the tier holds no more than its bound. A project needing
admission (W-TinyLFU) wraps `cachetools` or its own behind this port and proves it with
`sincpro_framework.caching.testing.EvictionContract`.
"""

from abc import ABC, abstractmethod


class Eviction(ABC):
    @abstractmethod
    def touched(self, key: str) -> None:
        """`key` was served — a key that is not tracked is left untracked."""

    @abstractmethod
    def admitted(self, key: str) -> list[str]:
        """`key` was kept; answer the keys the tier lets go of now — never `key` itself."""

    @abstractmethod
    def forgotten(self, key: str) -> None:
        """`key` was let go of — it is never answered as a victim afterwards."""
