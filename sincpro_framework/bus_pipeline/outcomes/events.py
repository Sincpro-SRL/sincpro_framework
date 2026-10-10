"""The two events every execution ends in: `ExecutionCompleted` when a use case answered,
`ExecutionFailed` when an exception escaped the call."""

from dataclasses import dataclass, field
from typing import Any

from sincpro_framework.ddd.events import DomainEvent

FAILURE_ID = "failure_id"
"""What an escaped exception carries: the id of the `ExecutionFailed` that went out with it."""


@dataclass(kw_only=True)
class ExecutionCompleted(DomainEvent):
    """A use case that answered — what it was handed, what it gave, where, in which flow."""

    name = "sincpro.execution.v1.completed"

    use_case: str = ""
    """The DTO that was executed, by its identity — `billing.CommandIssueInvoice`."""
    bus: str = ""
    """The bounded context it ran in."""
    level: str = ""
    """`feature` or `application_service`."""
    dto: dict[str, Any] = field(default_factory=dict)
    """The DTO as it was handed, as JSON values."""
    response: Any = None
    """What the handler returned, as JSON values — `None` when it returned nothing."""
    response_type: str = ""
    execution_id: str = ""
    """The execution that completed."""
    context: dict[str, Any] = field(default_factory=dict)
    """What the flow carried — the keys that travel, a `Secret` as its value."""


@dataclass(kw_only=True)
class ExecutionFailed(DomainEvent):
    """An exception that escaped a call — what failed, where, in which flow, and why."""

    name = "sincpro.execution.v1.failed"

    use_case: str = ""
    """The DTO whose execution raised, by its identity — `billing.CommandIssueInvoice`."""
    bus: str = ""
    """The bounded context it raised in."""
    level: str = ""
    """`feature` or `application_service`."""
    escaped_from: str = ""
    """The bus whose caller received the exception."""
    dto: dict[str, Any] = field(default_factory=dict)
    """The DTO as it was handed, as JSON values."""
    error_type: str = ""
    error: str = ""
    kind: str = ""
    """The `FailureKind` the failure is answered with on every wire."""
    retry_after: float | None = None
    """Seconds to wait before the same call is sent again; `None` when sending it again will not
    help."""
    execution_id: str = ""
    """The execution that failed."""
    context: dict[str, Any] = field(default_factory=dict)
    """What the flow carried — the keys that travel, a `Secret` as its value."""


OUTCOME_EVENTS = (ExecutionCompleted, ExecutionFailed)
"""What hearing an outcome produces is never announced: a listener that hands outcomes to a bus
would otherwise hear the outcome of its own hearing, without end."""
