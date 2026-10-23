"""`BusOutcomesMixin`: what a `UseFramework` mixes in to hear how its use cases ended — a function,
or a `Publisher` that hands each outcome on."""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from sincpro_framework.bus_pipeline.listeners import to_publisher
from sincpro_framework.bus_pipeline.outcomes.outcomes import Outcomes


class BusOutcomesMixin:
    """What a `UseFramework` adds to hear how its use cases ended."""

    _registrations: list[Callable[[Any], Any]]

    if TYPE_CHECKING:

        @property
        def name(self) -> str: ...

    def _init_outcomes(self) -> None:
        self._outcomes = Outcomes(self.name)

    def on_completion[L: Callable[..., Any]](self, listener: L) -> L:
        """Hear every use case of this bus that answered — an `ExecutionCompleted` with the DTO and
        the response, in this process. A decorator.

            @billing.on_completion
            def done(completed: ExecutionCompleted) -> None: ...

        Context: one per Feature and ApplicationService that returned, the Features an
        ApplicationService runs included; none when an error handler answered instead. It
        observes: it cannot change the answer, and one that raises is logged and reaches nothing
        else. A generation of this bus (`fresh`) hears it too. Every bus of the process:
        `sincpro_framework.bus_pipeline.outcomes.listeners.completions.subscribe(listener)`.
        """
        self._outcomes.completed.subscribe(listener)
        self._registrations.append(lambda bus: bus.on_completion(listener))
        return listener

    def publish_completions(self, to: Any) -> None:
        """Hand every use case of this bus that answered to `to` — a `Publisher` over any queue:
        another bus, a broker topic, the outbox. A delivery that fails is logged.

            billing.publish_completions(to=Publisher(FastStreamQueue(kafka)))
        """
        self._outcomes.completed.subscribe(to_publisher(to))
        self._registrations.append(lambda bus: bus.publish_completions(to))

    def on_failure[L: Callable[..., Any]](self, listener: L) -> L:
        """Hear every failure of this bus that escaped its call — an `ExecutionFailed`, in this
        process. A decorator.

            @billing.on_failure
            def alert(failure: ExecutionFailed) -> None: ...

        Context: emitted once, where the caller receives the exception, never when an error
        handler answered instead. It observes: it cannot change what the call raised, and one that
        raises is logged and reaches nothing else. A generation of this bus (`fresh`) hears it
        too. Every bus of the process:
        `sincpro_framework.bus_pipeline.outcomes.listeners.failures.subscribe(listener)`.
        """
        self._outcomes.failed.subscribe(listener)
        self._registrations.append(lambda bus: bus.on_failure(listener))
        return listener

    def publish_failures(self, to: Any) -> None:
        """Hand every failure of this bus that escaped its call to `to` — a `Publisher` over any
        queue: another bus, a broker topic (the error queue), the outbox. Fire and forget: a
        delivery that fails is logged.

            billing.publish_failures(to=Publisher(FastStreamQueue(kafka)))
        """
        self._outcomes.failed.subscribe(to_publisher(to))
        self._registrations.append(lambda bus: bus.publish_failures(to))
