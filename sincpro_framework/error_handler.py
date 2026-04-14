from typing import Any, Callable, Optional, TypeAlias

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
            raise exc

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
