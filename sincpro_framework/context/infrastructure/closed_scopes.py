"""What the `bus.context(...)` scopes opened and closed inside an execution said — collected,
because the execution's span, metrics and error are recorded after those scopes closed.

    with CLOSED_SCOPES.collecting():      the execution, from its first span to its last signal
        ...                               each scope closing calls CLOSED_SCOPES.record(its values)
        CLOSED_SCOPES.collected()         what they said, for the signals
"""

from collections.abc import Generator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any


class ClosedScopes:
    """The values of the scopes closed inside the execution in progress — per thread and task."""

    def __init__(self) -> None:
        """One `ContextVar` for the process: empty outside every execution."""
        self._collected: ContextVar[dict[str, Any] | None] = ContextVar(
            "sincpro_execution_said", default=None
        )

    @contextmanager
    def collecting(self) -> Generator[None, None, None]:
        """Collect, for the block, what every `bus.context(...)` scope closing inside it said.

        Context: the execution's span, metrics and error are recorded once the execution ends —
        by then the scopes it opened are closed and their values gone from the tree. Each closing
        scope `record`s its values here, so the signals still say them.

        1. A fresh, empty collection for this execution — a nested one starts its own.
        2. The block runs.
        Final: the previous collection comes back; a reset from another context (a generator
        closed elsewhere) is ignored rather than raised.

            with CLOSED_SCOPES.collecting():
                with billing.context({"company": 3}):
                    ...
                CLOSED_SCOPES.collected()     ->  {"company": 3}    # the scope closed; its values stay
            CLOSED_SCOPES.collected()         ->  {}                # outside, the collection before it

        A nested block collects apart: what its scopes say does not reach the outer one.
        """
        token = self._collected.set({})
        try:
            yield
        finally:
            try:
                self._collected.reset(token)
            except ValueError:
                pass

    def record(self, values: Mapping[str, Any]) -> None:
        """A `bus.context(...)` scope closing inside an execution: what it said still counts for
        the signals the execution records when it closes. Outside an execution, nothing. Never
        raises.

            with CLOSED_SCOPES.collecting():
                CLOSED_SCOPES.record({"company": 3})
                CLOSED_SCOPES.record({"company": 4, "lang": "es"})
                CLOSED_SCOPES.collected()     ->  {"company": 4, "lang": "es"}   # the later wins
            CLOSED_SCOPES.record({"company": 3})   ->  nothing collected, outside an execution

        Merged into the execution's collection, key by key — a later scope's value wins.
        """
        collected = self._collected.get()
        if collected is None:
            return
        try:
            collected.update(values)
        except Exception:
            pass

    def collected(self) -> dict[str, Any]:
        """What the scopes of the execution in progress said — empty outside one."""
        return self._collected.get() or {}


CLOSED_SCOPES = ClosedScopes()
"""The one collection of the process — what `bus.context(...)` records and the signals read."""
