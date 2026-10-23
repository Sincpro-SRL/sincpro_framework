"""`BusIncomesMixin`: what a `UseFramework` mixes in to hear its use cases begin — a function, or a
`Publisher` that hands each start on."""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from sincpro_framework.bus_pipeline.incomes.incomes import Incomes
from sincpro_framework.bus_pipeline.listeners import to_publisher


class BusIncomesMixin:
    """What a `UseFramework` adds to hear when its use cases begin."""

    _registrations: list[Callable[[Any], Any]]

    if TYPE_CHECKING:

        @property
        def name(self) -> str: ...

    def _init_incomes(self) -> None:
        self._incomes = Incomes(self.name)

    def on_start[L: Callable[..., Any]](self, listener: L) -> L:
        """Hear every use case of this bus that begins — an `ExecutionStarted` with the DTO, in
        this process, before the use case runs. A decorator.

            @billing.on_start
            def began(started: ExecutionStarted) -> None: ...

        Context: one per Feature and ApplicationService, the Features an ApplicationService runs
        included. It observes: it cannot change what the call answers, and one that raises is
        logged and reaches nothing else. A generation of this bus (`fresh`) hears it too. Every
        bus of the process: `sincpro_framework.bus_pipeline.incomes.listeners.starts.subscribe(listener)`.
        """
        self._incomes.started.subscribe(listener)
        self._registrations.append(lambda bus: bus.on_start(listener))
        return listener

    def publish_starts(self, to: Any) -> None:
        """Hand every use case of this bus that begins to `to` — a `Publisher` over any queue:
        another bus, a broker topic, the outbox. A delivery that fails is logged.

            billing.publish_starts(to=Publisher(FastStreamQueue(kafka)))
        """
        self._incomes.started.subscribe(to_publisher(to))
        self._registrations.append(lambda bus: bus.publish_starts(to))
