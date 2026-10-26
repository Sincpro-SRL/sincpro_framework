"""`StoreBypass`: a store that fails is bypassed for a while, not waited on at each call — the one
breaker every component that stands on a shared store uses.

    bypass = StoreBypass(timedelta(seconds=30), clock=time.time)
    bypass.trip()        the store did not answer: bypassed for 30 seconds from now
    bypass.is_open()     True until then — the caller answers without the store meanwhile

Context: a Redis that stops answering turns every call that reads it into a socket timeout, and
every call waits it out. The cache answers from the process tier meanwhile (FusionCache's
circuit breaker); a circuit breaker lets its calls through unguarded. The first call after
`bypass_for` tries the store again. How long one operation may take is the client's socket
timeout, not this.
"""

import threading
from collections.abc import Callable
from datetime import timedelta


class StoreBypass:
    """Open for `bypass_for` after any store failure; closed again by the clock alone."""

    def __init__(self, bypass_for: timedelta, clock: Callable[[], float]) -> None:
        """`clock` is the caller's own — seconds, moved by hand in a test."""
        self.bypass_for = bypass_for
        self.clock = clock
        self._open_until = 0.0
        self._lock = threading.Lock()

    def is_open(self) -> bool:
        """Whether the store is bypassed right now — no half-open state: once `bypass_for` has
        passed, every caller tries the store again."""
        return self.clock() < self._open_until

    def trip(self) -> None:
        """Opens the bypass for `bypass_for` from now — a failure while open extends it."""
        with self._lock:
            self._open_until = self.clock() + self.bypass_for.total_seconds()
