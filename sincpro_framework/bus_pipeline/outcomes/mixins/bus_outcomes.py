"""`BusOutcomesMixin`: what a `UseFramework` mixes in to hear how its use cases ended — a function,
or a `Publisher` that hands each outcome on."""

from collections.abc import Callable, Iterable
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
        """The outcomes of this bus, named after it — handed to the buses when built."""
        self._outcomes = Outcomes(self.name)

    def on_completion(
        self,
        listener: Callable[..., Any] | None = None,
        use_cases: Iterable[type | str] | None = None,
        when: Any = None,
    ) -> Any:
        """Hear every use case of this bus that answered — an `ExecutionCompleted` with the DTO and
        the response, in this process. A decorator, alone or with
        `use_cases` / `when` to hear less (`sincpro_framework.bus_pipeline.listeners.hearing`).

            @billing.on_completion
            def done(completed: ExecutionCompleted) -> None: ...

        Context: one per Feature and ApplicationService that returned, the Features an
        ApplicationService runs included; none when an error handler answered instead. It
        observes: it cannot change the answer, and one that raises is logged and reaches nothing
        else. A generation of this bus (`fresh`) hears it too. Every bus of the process:
        `sincpro_framework.bus_pipeline.outcomes.listeners.completion_listeners.subscribe(listener)`.
        """
        if listener is None:
            return lambda given: self.on_completion(given, use_cases, when)
        self._outcomes.completed.subscribe(listener, use_cases, when)
        self._registrations.append(lambda bus: bus.on_completion(listener, use_cases, when))
        return listener

    def publish_completions(
        self,
        to: Any,
        use_cases: Iterable[type | str] | None = None,
        when: Any = None,
    ) -> None:
        """Hand every use case of this bus that answered to `to` — a `Publisher` over any queue:
        another bus, a broker topic, the outbox. A delivery that fails is logged.
        `use_cases` / `when` hand on less, as a listener hears less.

            billing.publish_completions(to=Publisher(FastStreamQueue(kafka)))
        """
        self._outcomes.completed.subscribe(to_publisher(to), use_cases, when)
        self._registrations.append(lambda bus: bus.publish_completions(to, use_cases, when))

    def on_failure(
        self,
        listener: Callable[..., Any] | None = None,
        use_cases: Iterable[type | str] | None = None,
        when: Any = None,
    ) -> Any:
        """Hear every failure of this bus that escaped its call — an `ExecutionFailed`, in this
        process. A decorator, alone or with
        `use_cases` / `when` to hear less (`sincpro_framework.bus_pipeline.listeners.hearing`).

            @billing.on_failure
            def alert(failure: ExecutionFailed) -> None: ...

        Context: emitted once, where the caller receives the exception, never when an error
        handler answered instead. It observes: it cannot change what the call raised, and one that
        raises is logged and reaches nothing else. A generation of this bus (`fresh`) hears it
        too. Every bus of the process:
        `sincpro_framework.bus_pipeline.outcomes.listeners.failure_listeners.subscribe(listener)`.
        """
        if listener is None:
            return lambda given: self.on_failure(given, use_cases, when)
        self._outcomes.failed.subscribe(listener, use_cases, when)
        self._registrations.append(lambda bus: bus.on_failure(listener, use_cases, when))
        return listener

    def publish_failures(
        self,
        to: Any,
        use_cases: Iterable[type | str] | None = None,
        when: Any = None,
    ) -> None:
        """Hand every failure of this bus that escaped its call to `to` — a `Publisher` over any
        queue: another bus, a broker topic (the error queue), the outbox. Fire and forget: a
        delivery that fails is logged. `use_cases` / `when` hand on less.

            billing.publish_failures(to=Publisher(FastStreamQueue(kafka)))
        """
        self._outcomes.failed.subscribe(to_publisher(to), use_cases, when)
        self._registrations.append(lambda bus: bus.publish_failures(to, use_cases, when))
