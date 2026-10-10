"""`BusInterceptorsMixin`: what a `UseFramework` mixes in to wrap its use cases — registering an
interceptor, switching one off, and wrapping each handler when the bus is built."""

from typing import TYPE_CHECKING, Any, Callable, Sequence

from sincpro_log.logger import LoggerProxy

from sincpro_framework.bus_pipeline.interceptors.interceptor import (
    Interceptor,
    InterceptorRegistration,
    interceptors_for,
)
from sincpro_framework.common.ordering import DEFAULT_SEQUENCE, name_of, ordered
from sincpro_framework.exceptions import BusAlreadyBuilt, UnknownDTOToExecute

if TYPE_CHECKING:
    from sincpro_framework.bus import FrameworkBus


class BusInterceptorsMixin:
    """What a `UseFramework` adds to wrap its use cases."""

    was_initialized: bool
    _logger_name: str
    _registrations: list[Callable[[Any], Any]]

    if TYPE_CHECKING:

        @property
        def logger(self) -> LoggerProxy: ...

    def _init_interceptors(self) -> None:
        self._interceptors: list[InterceptorRegistration] = []
        self._interceptors_off: list[Interceptor] = []

    def interceptor[T: Interceptor](
        self,
        *commands: type,
        replaces: Interceptor | None = None,
        before: Sequence[Interceptor] = (),
        after: Sequence[Interceptor] = (),
        sequence: int = DEFAULT_SEQUENCE,
    ) -> Callable[[T], T]:
        """Run the decorated function around every execution of these Commands — all of this
        bus's when none is named — however they are reached.

            @billing.interceptor(CommandCreateInvoice)
            def credit_check(dto, call_next): ...

            @billing.interceptor(CommandCreateInvoice, replaces=credit_check)   in its place
            @billing.interceptor(CommandCreateInvoice, before=[credit_check])    outside it

        Context: the first to run is the outermost. They run by `before`/`after`, then
        `sequence` (lower first), then the order they were registered — the order every
        extension point uses, see `sincpro_framework.common.ordering`; a replacement takes the place
        of what it replaces and wraps the same Commands. Fixed when the bus is built; one
        registered later would never run, so it is refused. See `sincpro_framework.bus_pipeline.interceptors.interceptor`
        for what an interceptor may and may not do.
        """

        def register(interceptor: T) -> T:
            if self.was_initialized:
                raise BusAlreadyBuilt(
                    f"interceptor {interceptor.__qualname__} registered late: '{self._logger_name}' is already built, so it would "
                    "never be used — register it before the first execution"
                )
            self._interceptors.append(
                InterceptorRegistration(
                    interceptor, commands, sequence, tuple(before), tuple(after), replaces
                )
            )
            self._registrations.append(
                lambda bus: bus.interceptor(
                    *commands,
                    replaces=replaces,
                    before=before,
                    after=after,
                    sequence=sequence,
                )(interceptor)
            )
            return interceptor

        return register

    def without_interceptor(self, interceptor: Interceptor) -> None:
        """Switch off an interceptor registered on this bus — it wraps nothing from then on.
        Refused once the bus is built, like registering one."""
        if self.was_initialized:
            raise BusAlreadyBuilt(
                f"switching off interceptor {interceptor.__qualname__} registered late: '{self._logger_name}' is already built, so it would "
                "never be used — register it before the first execution"
            )
        self._interceptors_off.append(interceptor)
        self._registrations.append(lambda bus: bus.without_interceptor(interceptor))

    def _ordered_interceptors(self) -> list[InterceptorRegistration]:
        """The interceptors in the order they wrap, outermost first — see
        `sincpro_framework.common.ordering`. What was asked and could not be done as said — a
        replacement that wraps other Commands than the one it replaces, one that replaces or
        switches off what is not registered — is a warning, never a refusal."""
        by_interceptor = {one.interceptor: one for one in self._interceptors}
        result = ordered(
            [one.placement for one in self._interceptors], self._interceptors_off
        )
        for note in result.notes:
            self.logger.warning(f"interceptor: {note}")
        for replacement, chain in result.replaced.items():
            wraps, wrapped = (
                by_interceptor[replacement].commands,
                by_interceptor[chain[-1]].commands,
            )
            if set(wraps) != set(wrapped):
                self.logger.warning(
                    f"interceptor {name_of(replacement)} replaces {name_of(chain[-1])} but wraps "
                    f"{', '.join(one.__name__ for one in wraps) or 'every Command'}, where that "
                    f"one wrapped {', '.join(one.__name__ for one in wrapped) or 'every Command'}"
                )
            self.logger.info(
                f"interceptor {name_of(chain[-1])} is replaced by {name_of(replacement)}"
            )
        for switched_off in result.off:
            self.logger.info(f"interceptor {name_of(switched_off)} is switched off")
        return [by_interceptor[one] for one in result.items]

    def _wrap_handlers(self, bus: "FrameworkBus") -> None:
        """Each handler's interceptors, outermost first — refused when one wraps a Command no
        Feature or ApplicationService of this bus answers."""
        feature_bus, app_service_bus = bus.feature_bus, bus.app_service_bus
        answered = {*feature_bus.feature_registry, *app_service_bus.app_service_registry}
        interceptors = self._ordered_interceptors()
        for registration in interceptors:
            for command in registration.commands:
                if command not in answered:
                    raise UnknownDTOToExecute(
                        f"interceptor {registration.name} wraps {command.__name__}, which no "
                        f"Feature or ApplicationService on '{self._logger_name}' answers"
                    )
        feature_bus.interceptors = {
            command: interceptors_for(command, interceptors)
            for command in feature_bus.feature_registry
        }
        app_service_bus.interceptors = {
            command: interceptors_for(command, interceptors)
            for command in app_service_bus.app_service_registry
        }
