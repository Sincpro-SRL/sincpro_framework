"""Circuits: stop calling a dependency that keeps failing, and try it again once it may be back.

    from sincpro_framework.circuits import Circuit

    siat = Circuit("siat")
    with siat: ...          @siat def send(...): ...          siat.call(send, ...)

A circuit is part of the core and needs no library: its state is kept where the context keeps its
leases — this process's memory, or the store every replica shares — and an open one fails with
`CircuitOpen`, `UNAVAILABLE` with its `retry_after`, which every wire, a pipeline's `Retry` and a
queue already know what to do with. A bus hosted by another service has one when asked:
`?circuit=on` on its address, or `hosted_by(address, circuit=True)`.

    circuit.py    Circuit, CircuitOpen, CircuitState — and what counts as a failure (COUNTED)
"""

from sincpro_framework.circuits.circuit import (
    COUNTED,
    Circuit,
    CircuitOpen,
    CircuitState,
    counted_by_default,
)

__all__ = ["COUNTED", "Circuit", "CircuitOpen", "CircuitState", "counted_by_default"]
