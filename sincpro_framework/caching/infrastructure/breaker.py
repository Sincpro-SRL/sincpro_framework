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


class StoreBreaker:
    def __init__(self, bypass_for: timedelta, clock: Callable[[], float]) -> None:
        self.bypass_for = bypass_for
        self.clock = clock
        self._open_until = 0.0
        self._lock = threading.Lock()

    def is_open(self) -> bool:
        return self.clock() < self._open_until

    def trip(self) -> None:
        with self._lock:
            self._open_until = self.clock() + self.bypass_for.total_seconds()
