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
    """Collect, for the block, what every `bus.context(...)` scope closing inside it said.

    Context: the execution's span, metrics and error are recorded once the execution ends — by
    then the scopes it opened are closed and their values gone from the tree. Each closing
    scope `remember`s its values here, so the signals still say them.

    1. A fresh, empty collection for this execution — a nested one starts its own.
    2. The block runs.
    Final: the previous collection comes back; a reset from another context (a generator
    closed elsewhere) is ignored rather than raised.

        with keeping_what_scopes_say():
            with billing.context({"company": 3}):
                ...
            said()     ->  {"company": 3}       # the scope closed; what it said is kept
        said()         ->  {}                   # outside, the collection before it

    A nested block collects apart: what its scopes say does not reach the outer one.
    """
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

        with keeping_what_scopes_say():
            remember({"company": 3}); remember({"company": 4, "lang": "es"})
            said()                    ->  {"company": 4, "lang": "es"}   # the later wins
        remember({"company": 3})      ->  nothing kept, outside an execution

    Merged into the execution's collection, key by key — a later scope's value wins.
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
