"""The two events an execution ends in: `ExecutionCompleted` when a use case answered,
`ExecutionFailed` when an exception escaped the call."""

from dataclasses import dataclass
from typing import Any

from sincpro_framework.bus_pipeline.events import ExecutionEvent


@dataclass(kw_only=True)
class ExecutionCompleted(ExecutionEvent):
    """A use case that answered — what it was handed, what it gave, where, in which flow."""

    name = "sincpro.execution.v1.completed"

    response: Any = None
    """What the handler returned, as JSON values — `None` when it returned nothing."""
    response_type: str = ""


@dataclass(kw_only=True)
class ExecutionFailed(ExecutionEvent):
    """An exception that escaped a call — what failed, where, in which flow, and why."""

    name = "sincpro.execution.v1.failed"

    escaped_from: str = ""
    """The bus whose caller received the exception."""
    error_type: str = ""
    error: str = ""
    kind: str = ""
    """The `FailureKind` the failure is answered with on every wire."""
    fault: bool = True
    """Whether it is a problem of the service — a crash, a service down — or an answer the caller
    was meant to get (`False`): what a listener that pages somebody filters on,
    `when=Condition(field="fault", value=True)`."""
    retry_after: float | None = None
    """Seconds to wait before the same call is sent again; `None` when sending it again will not
    help."""
