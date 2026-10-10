from typing import TYPE_CHECKING, Any, Callable, Optional, Sequence, TypeAlias

from sincpro_framework.common.ordering import DEFAULT_SEQUENCE, Placement, name_of, ordered

if TYPE_CHECKING:
    from sincpro_framework.bus import FrameworkBus
    from sincpro_framework.ioc import FrameworkContainer

ErrorHandler: TypeAlias = Callable[[Exception], Any]
"""Handler signature: receives the error and returns a result or re-raises.

On re-raise the framework automatically delegates to the next handler in the chain.

Example::

    def my_handler(error: Exception) -> Any:
        return ErrorResponse(str(error))

    def logging_handler(error: Exception) -> Any:
        log.error(error)
        raise error  # delegates to the next handler
"""


def compose_handler(
    handler: ErrorHandler,
    next_handler: Optional[ErrorHandler],
) -> ErrorHandler:
    """Wrap ``handler`` so that on re-raise ``next_handler`` is called automatically.

    Args:
        handler: The handler to wrap.
        next_handler: The next handler in the chain (``None`` for the last one).

    Returns:
        A single composed ``ErrorHandler``.
    """

    def _wrapped(error: Exception) -> Any:
        try:
            return handler(error)
        except Exception as exc:
            if next_handler:
                return next_handler(exc)
            raise

    return _wrapped


def build_error_handler_chain(handlers: list[ErrorHandler]) -> Optional[ErrorHandler]:
    """Build a handler chain where the first registered handler executes first.

    Args:
        handlers: Handlers in registration order.

    Returns:
        A single composed ``ErrorHandler``, or ``None`` if the list is empty.
    """
    if not handlers:
        return None
    chain: Optional[ErrorHandler] = None
    for h in reversed(handlers):
        chain = compose_handler(h, chain)
    return chain


class BusErrorHandlersMixin:
    """What a `UseFramework` mixes in to answer errors: three chains — global, feature and
    application service — each in the order every extension point uses, handed to the buses
    when they are built."""

    was_initialized: bool
    bus: "FrameworkBus | None"
    _registrations: list[Callable[[Any], Any]]
    _sp_container: "FrameworkContainer"

    def _init_error_handlers(self) -> None:
        self._global_error_handlers: list[Placement] = []
        self._feature_error_handlers: list[Placement] = []
        self._app_service_error_handlers: list[Placement] = []
        self._error_handlers_off: list[ErrorHandler] = []
        self.global_error_handler: Optional[ErrorHandler] = None
        self.feature_error_handler: Optional[ErrorHandler] = None
        self.app_service_error_handler: Optional[ErrorHandler] = None

    def _add_error_handlers_provided_by_user(self):
        if self.global_error_handler:
            self._sp_container.framework_bus.add_attributes(
                handle_error=self.global_error_handler
            )

        if self.feature_error_handler:
            self._sp_container.feature_bus.add_attributes(
                handle_error=self.feature_error_handler
            )

        if self.app_service_error_handler:
            self._sp_container.app_service_bus.add_attributes(
                handle_error=self.app_service_error_handler
            )

    def _error_chain_notes(self) -> list[str]:
        """What the three chains were asked and could not do as said — told when the bus is
        built, once, rather than at every handler added."""
        chains = (
            self._global_error_handlers,
            self._feature_error_handlers,
            self._app_service_error_handlers,
        )
        registered = {one.item for chain in chains for one in chain}
        notes = [
            note
            for chain in chains
            for note in ordered(
                chain, [one for one in self._error_handlers_off if one in registered]
            ).notes
        ]
        return notes + [
            f"{name_of(handler)} is switched off, but it is not registered here"
            for handler in self._error_handlers_off
            if handler not in registered
        ]

    def _error_chain(self, placements: list[Placement]) -> Optional[ErrorHandler]:
        registered = {one.item for one in placements}
        off = [one for one in self._error_handlers_off if one in registered]
        return build_error_handler_chain(ordered(placements, off).items)

    def _apply_error_chains(self) -> None:
        """Each chain in the order every extension point uses — first to run is the first to
        see the error — and handed to the bus at once when it is already built."""
        self.global_error_handler = self._error_chain(self._global_error_handlers)
        self.feature_error_handler = self._error_chain(self._feature_error_handlers)
        self.app_service_error_handler = self._error_chain(self._app_service_error_handlers)
        if self.was_initialized and self.bus is not None:
            self.bus.handle_error = self.global_error_handler
            self.bus.feature_bus.handle_error = self.feature_error_handler
            self.bus.app_service_bus.handle_error = self.app_service_error_handler

    def _add_error_handler(
        self,
        placements: list[Placement],
        handler: ErrorHandler,
        replaces: ErrorHandler | None,
        before: Sequence[ErrorHandler],
        after: Sequence[ErrorHandler],
        sequence: int,
    ) -> None:
        if not callable(handler):
            raise TypeError("The handler must be a callable")
        placements.append(Placement(handler, sequence, tuple(before), tuple(after), replaces))
        self._apply_error_chains()

    def without_error_handler(self, handler: ErrorHandler) -> None:
        """Switch off an error handler of any of the three kinds — one registered later too;
        one never registered is a warning when the bus is built."""
        self._error_handlers_off.append(handler)
        self._registrations.append(lambda bus: bus.without_error_handler(handler))
        self._apply_error_chains()

    def add_global_error_handler(
        self,
        handler: ErrorHandler,
        replaces: ErrorHandler | None = None,
        before: Sequence[ErrorHandler] = (),
        after: Sequence[ErrorHandler] = (),
        sequence: int = DEFAULT_SEQUENCE,
    ) -> None:
        """Register a global error handler.

        Signature: ``(error) -> Any``. First registered = first to execute.

        **What the handler returns becomes the bus's answer, and the error is gone.**
        Re-raise to pass it on: to the next handler in the chain, or — from the last one — to
        whoever called the bus. This is the part that catches people out, because a handler
        written to *watch* errors is the common case and returns ``None`` without meaning to::

            app.add_global_error_handler(lambda error: log.error(error))
            app(SomeCommand())        # returns None. The failure is gone and nobody knows.

        A handler that only wants to look at the error ends with ``raise``::

            def logger(error):
                log.error(error)
                raise error           # seen, and still an error

        With no handler registered at all, the exception simply propagates.

        Example of the two kinds together::

            def base(error):
                return ErrorResponse(str(error))    # answers, and stops here

            app.add_global_error_handler(base)

            def logger(error):
                log.error(error)
                raise error                          # delegates to base

            app.add_global_error_handler(logger)
        """
        self._add_error_handler(
            self._global_error_handlers, handler, replaces, before, after, sequence
        )
        self._registrations.append(
            lambda bus: bus.add_global_error_handler(
                handler, replaces, before, after, sequence
            )
        )

    def add_feature_error_handler(
        self,
        handler: ErrorHandler,
        replaces: ErrorHandler | None = None,
        before: Sequence[ErrorHandler] = (),
        after: Sequence[ErrorHandler] = (),
        sequence: int = DEFAULT_SEQUENCE,
    ) -> None:
        """Register a feature-level error handler, for errors raised inside a `Feature`.

        Same semantics as `add_global_error_handler`, including the one that surprises people:
        what the handler returns becomes the bus's answer, and only a re-raise passes the error
        on.
        """
        self._add_error_handler(
            self._feature_error_handlers, handler, replaces, before, after, sequence
        )
        self._registrations.append(
            lambda bus: bus.add_feature_error_handler(
                handler, replaces, before, after, sequence
            )
        )

    def add_app_service_error_handler(
        self,
        handler: ErrorHandler,
        replaces: ErrorHandler | None = None,
        before: Sequence[ErrorHandler] = (),
        after: Sequence[ErrorHandler] = (),
        sequence: int = DEFAULT_SEQUENCE,
    ) -> None:
        """Register an error handler for errors raised inside an `ApplicationService`.

        Same semantics as `add_global_error_handler`, including the one that surprises people:
        what the handler returns becomes the bus's answer, and only a re-raise passes the error
        on.
        """
        self._add_error_handler(
            self._app_service_error_handlers, handler, replaces, before, after, sequence
        )
        self._registrations.append(
            lambda bus: bus.add_app_service_error_handler(
                handler, replaces, before, after, sequence
            )
        )
