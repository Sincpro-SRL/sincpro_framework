"""`BusInterceptorsMixin`: what a `UseFramework` mixes in to wrap its use cases — registering an
interceptor, switching one off, and wrapping each handler when the bus is built."""

from threading import RLock
from typing import TYPE_CHECKING, Any, Callable, Sequence

from sincpro_log.logger import LoggerProxy

from sincpro_framework import exceptions
from sincpro_framework.bus_pipeline.interceptors.interceptor import (
    Intercepting,
    Interceptor,
    InterceptorCall,
    InterceptorRegistration,
    interceptors_for,
)
from sincpro_framework.common.ordering import DEFAULT_SEQUENCE, name_of, ordered

if TYPE_CHECKING:
    from sincpro_framework.bus import FrameworkBus


class BusInterceptorsMixin:
    """What a `UseFramework` adds to wrap its use cases."""

    _build_lock: RLock

    if TYPE_CHECKING:

        @property
        def name(self) -> str: ...

        @property
        def logger(self) -> LoggerProxy: ...

        def _registered(self, replay: Callable[[Any], Any], what: str) -> None: ...

        dynamic_dep_registry: dict[str, Any]

    def _init_interceptors(self) -> None:
        """No interceptors registered, none switched off."""
        self._interceptors: list[InterceptorRegistration] = []
        self._interceptors_off: list[Intercepting] = []

    def interceptor[T: Intercepting](
        self,
        *commands: type,
        replaces: Intercepting | None = None,
        before: Sequence[Intercepting] = (),
        after: Sequence[Intercepting] = (),
        sequence: int = DEFAULT_SEQUENCE,
        subclasses: bool = False,
    ) -> Callable[[T], T]:
        """Run the decorated function around every execution of these Commands — all of this
        bus's when none is named — however they are reached.

            @billing.interceptor(CommandCreateInvoice)
            def credit_check(dto, call_next): ...

            @billing.interceptor(CommandCreateInvoice, replaces=credit_check)   in its place
            @billing.interceptor(CommandCreateInvoice, before=[credit_check])    outside it
            @billing.interceptor(WriteCommand, subclasses=True)                  every write
            @billing.interceptor(CommandCreateInvoice)
            class CheckCredit(Interceptor): ...       a class: dependencies injected, typed

        Context: a Command named is wrapped as it is, never its subclasses — `subclasses=True`
        wraps them too, and the base need not be answered by any handler. The first to run is
        the outermost. They run by `before`/`after`, then
        `sequence` (lower first), then the order they were registered — the order every
        extension point uses, see `sincpro_framework.common.ordering`; a replacement takes the place
        of what it replaces and wraps the same Commands. Registered after the bus was built, it
        wraps from the next execution. See `sincpro_framework.bus_pipeline.interceptors.interceptor`
        for what an interceptor may and may not do.
        """

        def register(interceptor: T) -> T:
            """The registration stored, and its replay for every new generation of the bus — on
            a bus already built it wraps from the next execution; the function is answered
            unchanged, so it can still be called or named by `replaces`."""
            with self._build_lock:
                self._interceptors.append(
                    InterceptorRegistration(
                        interceptor,
                        commands,
                        sequence,
                        tuple(before),
                        tuple(after),
                        replaces,
                        subclasses,
                    )
                )
                self._registered(
                    lambda bus: bus.interceptor(
                        *commands,
                        replaces=replaces,
                        before=before,
                        after=after,
                        sequence=sequence,
                        subclasses=subclasses,
                    )(interceptor),
                    f"interceptor {name_of(interceptor)}",
                )
            return interceptor

        return register

    def without_interceptor(self, interceptor: Intercepting) -> None:
        """Switch off an interceptor registered on this bus — it wraps nothing from then on, or
        from the next execution when the bus was already built."""
        with self._build_lock:
            self._interceptors_off.append(interceptor)
            self._registered(
                lambda bus: bus.without_interceptor(interceptor),
                f"switching off interceptor {name_of(interceptor)}",
            )

    def _ordered_interceptors(
        self, attached: Sequence[InterceptorRegistration]
    ) -> list[InterceptorRegistration]:
        """The interceptors in the order they wrap, outermost first — the ones registered, then
        `attached` at build — see `sincpro_framework.common.ordering`. What was asked and could
        not be done as said — a replacement that wraps other Commands than the one it replaces,
        one that replaces or switches off what is not registered — is a warning, never a
        refusal."""
        registrations = [*self._interceptors, *attached]
        by_interceptor = {one.interceptor: one for one in registrations}
        result = ordered([one.placement for one in registrations], self._interceptors_off)
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

    def _call_of(self, item: Intercepting) -> InterceptorCall:
        """What the chain runs for a registered interceptor: a function as it is; for a class of
        `Interceptor`, the `intercept` of one made for this generation — this bus's
        dependencies set on it, bound to its context."""
        if not (isinstance(item, type) and issubclass(item, Interceptor)):
            return item  # type: ignore[return-value]
        made = item()
        for name, dependency in self.dynamic_dep_registry.items():
            setattr(made, name, dependency)
        made.bind_to_framework(self)
        return made.intercept

    def _wrap_handlers(
        self, bus: "FrameworkBus", attached: Sequence[InterceptorRegistration]
    ) -> None:
        """Each handler's interceptors, outermost first — the ones registered and those
        `attached` at build, as the recorders of the pipelines are — refused when one wraps a
        Command no Feature or ApplicationService of this bus answers."""
        feature_bus, app_service_bus = bus.feature_bus, bus.app_service_bus
        answered = {*feature_bus.feature_registry, *app_service_bus.app_service_registry}
        interceptors = self._ordered_interceptors(attached)
        for registration in interceptors:
            if registration.subclasses:
                if registration.commands and not any(
                    registration.wraps(command) for command in answered
                ):
                    self.logger.warning(
                        f"interceptor {registration.name} wraps "
                        f"{', '.join(one.__name__ for one in registration.commands)} and its "
                        f"subclasses, and no Feature or ApplicationService on '{self.name}' "
                        "answers any of them"
                    )
                continue
            for command in registration.commands:
                if command not in answered:
                    raise exceptions.UnknownDTOToExecute(
                        f"interceptor {registration.name} wraps {command.__name__}, which no "
                        f"Feature or ApplicationService on '{self.name}' answers"
                    )
        calls = {one.interceptor: self._call_of(one.interceptor) for one in interceptors}
        feature_bus.interceptors = {
            command: interceptors_for(command, interceptors, calls)
            for command in feature_bus.feature_registry
        }
        app_service_bus.interceptors = {
            command: interceptors_for(command, interceptors, calls)
            for command in app_service_bus.app_service_registry
        }
