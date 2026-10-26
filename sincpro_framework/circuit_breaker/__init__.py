"""Circuit breaker: stop calling a dependency that fails repeatedly, and try it again once it may be back.

    from sincpro_framework.circuit_breaker import CircuitBreaker

    siat = CircuitBreaker("siat")
    with siat: ...          @siat def send(...): ...          siat.call(send, ...)

A circuit is part of the core and needs no library: its state is stored where the context stores its
leases — this process's memory, or the store every replica shares — and an open one fails with
`CircuitOpen`, `UNAVAILABLE` with its `retry_after`, which every wire, a pipeline's `Retry` and a
queue already know what to do with. A bus hosted by another service has one when asked:
`?circuit_breaker=on` on its address, or `hosted_by(address, circuit_breaker=True)`.

    circuit_breaker.py    CircuitBreaker, CircuitOpen, CircuitState — and what counts as a failure (COUNTED)
"""

from sincpro_framework.circuit_breaker.circuit_breaker import (
    COUNTED,
    CircuitBreaker,
    CircuitOpen,
    CircuitState,
    counted_by_default,
)

__all__ = ["COUNTED", "CircuitBreaker", "CircuitOpen", "CircuitState", "counted_by_default"]
