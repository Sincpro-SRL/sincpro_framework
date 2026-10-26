"""`BusObservability`: what the metrics ask of the bus that runs a use case (PRD_03 §4.9)."""

from typing import Protocol

from sincpro_framework.observability.domain import ObservabilityIdentity


class BusObservability(Protocol):
    """What the metrics ask of the bus that runs a use case — its `Observability`: who it is,
    and whether an error is a fault, as it judges it. Read only when something records,
    and every read is shielded: a bus that answers neither is measured all the same."""

    @property
    def identity(self) -> ObservabilityIdentity:
        """Who the bus is — the service and version its series name."""
        ...

    def faulted(self, error: BaseException) -> bool:
        """Whether the bus judges `error` a problem of the service — `sincpro.fault`."""
        ...
