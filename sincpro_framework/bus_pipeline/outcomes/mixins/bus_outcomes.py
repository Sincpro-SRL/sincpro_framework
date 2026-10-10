"""`BusOutcomesMixin`: what a `UseFramework` mixes in to hear how its executions ended — a
function, or a `Publisher` that hands each outcome on."""

from typing import Any, Callable

from sincpro_framework.bus_pipeline.outcomes.listeners import (
    completions,
    failures,
    to_publisher,
)


class BusOutcomesMixin:
    """What a `UseFramework` adds to hear its outcomes."""

    _logger_name: str

    def on_completion[L: Callable[..., Any]](self, listener: L) -> L:
        """Hear every use case of this bus that answered — an `ExecutionCompleted` with the DTO and
        the response, in this process. A decorator.

            @billing.on_completion
            def done(completed: ExecutionCompleted) -> None: ...

        Context: one per Feature and ApplicationService that returned, the Features an
        ApplicationService runs included; none when an error handler answered instead. A listener
        that raises is logged and reaches nothing else. Every bus of the process:
        `sincpro_framework.bus_pipeline.outcomes.listeners.completions.subscribe(listener)`.
        """
        completions.subscribe(listener, self._logger_name)
        return listener

    def publish_completions(self, to: Any) -> None:
        """Hand every use case of this bus that answered to `to` — a `Publisher` over any queue:
        another bus, a broker topic, the outbox. A delivery that fails is logged.

            billing.publish_completions(to=Publisher(FastStreamQueue(kafka)))
        """
        completions.subscribe(to_publisher(to), self._logger_name)

    def on_failure[L: Callable[..., Any]](self, listener: L) -> L:
        """Hear every failure of this bus that escaped its call — an `ExecutionFailed`, in this
        process. A decorator.

            @billing.on_failure
            def alert(failure: ExecutionFailed) -> None: ...

        Context: emitted once, where the caller receives the exception, never when an error
        handler answered instead. A listener that raises is logged and reaches nothing else. Every
        bus of the process: `sincpro_framework.bus_pipeline.outcomes.listeners.failures.subscribe(listener)`.
        """
        failures.subscribe(listener, self._logger_name)
        return listener

    def publish_failures(self, to: Any) -> None:
        """Hand every failure of this bus that escaped its call to `to` — a `Publisher` over any
        queue: another bus, a broker topic (the error queue), the outbox. Fire and forget: a
        delivery that fails is logged.

            billing.publish_failures(to=Publisher(FastStreamQueue(kafka)))
        """
        failures.subscribe(to_publisher(to), self._logger_name)
