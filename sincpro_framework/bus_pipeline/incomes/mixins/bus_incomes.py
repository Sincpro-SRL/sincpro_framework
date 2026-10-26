"""`BusIncomesMixin`: what a `UseFramework` mixes in to hear its use cases begin — a function, or a
`Publisher` that hands each start on."""

from collections.abc import Callable, Iterable
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
        """The incomes of this bus, named after it — handed to both layer buses when built."""
        self._incomes = Incomes(self.name)

    def on_start(
        self,
        listener: Callable[..., Any] | None = None,
        use_cases: Iterable[type | str] | None = None,
        when: Any = None,
    ) -> Any:
        """Hear every use case of this bus that begins — an `ExecutionStarted` with the DTO, in
        this process, before the use case runs. A decorator, alone or with
        `use_cases` / `when` to hear less (`sincpro_framework.bus_pipeline.listeners.hearing`).

            @billing.on_start
            def began(started: ExecutionStarted) -> None: ...

        Context: one per Feature and ApplicationService, the Features an ApplicationService runs
        included. It observes: it cannot change what the call answers, and one that raises is
        logged and reaches nothing else. A generation of this bus (`fresh`) hears it too. Every
        bus of the process: `sincpro_framework.bus_pipeline.incomes.listeners.start_listeners.subscribe(listener)`.
        """
        if listener is None:
            return lambda given: self.on_start(given, use_cases, when)
        self._incomes.started.subscribe(listener, use_cases, when)
        self._registrations.append(lambda bus: bus.on_start(listener, use_cases, when))
        return listener

    def publish_starts(
        self,
        to: Any,
        use_cases: Iterable[type | str] | None = None,
        when: Any = None,
    ) -> None:
        """Hand every use case of this bus that begins to `to` — a `Publisher` over any queue:
        another bus, a broker topic, the outbox. A delivery that fails is logged.
        `use_cases` / `when` hand on less, as a listener hears less.

            billing.publish_starts(to=Publisher(FastStreamQueue(kafka)))
        """
        self._incomes.started.subscribe(to_publisher(to), use_cases, when)
        self._registrations.append(lambda bus: bus.publish_starts(to, use_cases, when))
