"""The outcome of an execution, as an event anyone may listen to: `ExecutionCompleted` when a use
case answered, `ExecutionFailed` when an exception escaped the call.

Context: the events load with the first who asks for them — a process nobody listens in never
loads them, nor the DDD layer they are built on.
"""

from typing import TYPE_CHECKING, Any

from sincpro_framework.bus_pipeline.outcomes.listeners import OutcomeListener

if TYPE_CHECKING:
    from sincpro_framework.bus_pipeline.outcomes.events import (
        ExecutionCompleted,
        ExecutionFailed,
    )


def __getattr__(name: str) -> Any:
    if name in ("ExecutionCompleted", "ExecutionFailed"):
        from sincpro_framework.bus_pipeline.outcomes import events

        return getattr(events, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["ExecutionCompleted", "ExecutionFailed", "OutcomeListener"]
