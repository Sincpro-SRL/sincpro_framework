"""The store breaker: a shared store that fails is bypassed for a while, not waited on each call.

Context: a Redis that stops answering turns every cached call into a socket timeout, and every
call waits it out — the cache that was there to spare the source now slows every answer down.
Any exception from a store operation opens the breaker for `bypass_for`; while it is open the
process tier stands in (FusionCache's circuit breaker), and the first call after it tries the
store again. How long one operation may take is the client's socket timeout, not this.
"""

import threading
from collections.abc import Callable
from datetime import timedelta


class StoreCircuitBreaker:
    """Open for `bypass_for` after any store failure; closed again by the clock alone."""

    def __init__(self, bypass_for: timedelta, clock: Callable[[], float]) -> None:
        """`clock` is the cache's own — seconds, moved by hand in a test."""
        self.bypass_for = bypass_for
        self.clock = clock
        self._open_until = 0.0
        self._lock = threading.Lock()

    def is_open(self) -> bool:
        """Whether the store is bypassed right now — no half-open state: once `bypass_for` has
        passed, every caller tries the store again."""
        return self.clock() < self._open_until

    def trip(self) -> None:
        """Opens the breaker for `bypass_for` from now — a failure while open extends it."""
        with self._lock:
            self._open_until = self.clock() + self.bypass_for.total_seconds()
