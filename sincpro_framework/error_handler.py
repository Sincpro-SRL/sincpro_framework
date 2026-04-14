import inspect
from typing import Any, Callable, Optional, TypeAlias

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

ErrorHandler: TypeAlias = Callable[[Exception], Any]
"""Standard handler signature. The framework calls the next handler automatically on re-raise.

Example::

    def my_handler(error: Exception) -> Any:
        return ErrorResponse(str(error))

    # Delegate to the next handler in the chain
    def logging_handler(error: Exception) -> Any:
        log.error(error)
        raise error  # framework will call the previous handler
"""

ErrorHandlerWithNext: TypeAlias = Callable[[Exception, Optional[ErrorHandler]], Any]
"""Advanced handler signature. The handler controls when and whether to call the next handler.

Example::

    def auditor(error: Exception, next_handler: ErrorHandler | None) -> Any:
        log_audit(error)
        if next_handler:
            return next_handler(error)
        raise error
"""

AnyErrorHandler: TypeAlias = ErrorHandler | ErrorHandlerWithNext
"""Union of both supported handler signatures. Use this when accepting any handler."""


# ---------------------------------------------------------------------------
# Composition logic
# ---------------------------------------------------------------------------


def compose_handler(
    handler: AnyErrorHandler,
    previous: Optional[ErrorHandler],
) -> ErrorHandler:
    """Compose a new error handler on top of the existing one.

    Detects the handler signature automatically:

    - ``(error)``               → **implicit**: if the handler re-raises, ``previous``
      is called automatically by the framework.
    - ``(error, next_handler)`` → **explicit**: the handler receives ``previous`` directly
      and decides when (or whether) to call it.

    Args:
        handler: The new handler to register.
        previous: The previously registered handler, or ``None`` if this is the first one.

    Returns:
        A single composed ``ErrorHandler`` that encapsulates the full chain.

    Example::

        # Build a chain incrementally
        chain = compose_handler(base_handler, None)
        chain = compose_handler(logging_handler, chain)
        chain = compose_handler(auditor_handler, chain)

        # When an error occurs, auditor → logging → base (last added = outermost)
    """
    n_params = len(inspect.signature(handler).parameters)

    if n_params >= 2:

        def _explicit(error: Exception) -> Any:
            return handler(error, previous)  # type: ignore[call-arg]

        return _explicit

    else:

        def _implicit(error: Exception) -> Any:
            try:
                return handler(error)  # type: ignore[call-arg]
            except Exception as exc:
                if previous:
                    return previous(exc)
                raise exc

        return _implicit
