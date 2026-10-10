"""What the `bus.context(...)` scopes opened and closed inside an execution said — kept, because
the execution's span, metrics and error are recorded after those scopes closed.

    with keeping_what_scopes_say():      the execution, from its first span to its last signal
        ...                              each scope closing calls remember(its values)
        said()                           what they said, for the signals
"""

from collections.abc import Generator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

_said: ContextVar[dict[str, Any] | None] = ContextVar("sincpro_execution_said", default=None)


@contextmanager
def keeping_what_scopes_say() -> Generator[None, None, None]:
    token = _said.set({})
    try:
        yield
    finally:
        try:
            _said.reset(token)
        except ValueError:
            pass


def remember(scope: Mapping[str, Any]) -> None:
    """A `bus.context(...)` scope closing inside an execution: what it said still counts for the
    signals the execution records when it closes. Outside an execution, nothing. Never raises.
    """
    kept = _said.get()
    if kept is None:
        return
    try:
        kept.update(scope)
    except Exception:
        pass


def said() -> dict[str, Any]:
    """What the scopes of the execution in progress said — empty outside one."""
    return _said.get() or {}
