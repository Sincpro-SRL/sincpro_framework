"""The outcome of an execution, as an event anyone may listen to: `ExecutionCompleted` when a use case
answered, `ExecutionFailed` when an exception escaped the call.

    @billing.on_completion                               a function, in this process
    def done(completed: ExecutionCompleted) -> None: ...
    @billing.on_failure
    def alert(failure: ExecutionFailed) -> None: ...

    billing.publish_completions(to=Publisher(queue))     a queue: another bus, a broker topic
    billing.publish_failures(to=Publisher(queue))
    completions.subscribe(audit) · failures.subscribe(audit)     every bus of the process

    try:
        billing(CommandIssueInvoice(...))
    except Exception as error:
        error.failure_id                                 the event that went out with it

**A completion is every use case that answered.** Each Feature and ApplicationService whose handler
returned — the Features an ApplicationService runs included, each its own — with the DTO it was
handed and the response it gave. One an error handler answered for did not complete: no event.

**A failure goes with the exception.** It is emitted when an exception escapes the whole call — once,
where the caller receives it — and never when an error handler answered instead: whoever contained
the error decided it does not travel. A failure caught by a handler's own `try` never escaped either.

**Both say where and in which flow.** `use_case`, `bus` and `level` name the use case; `execution_id`
is the execution, `causation_id` names it too, `correlation_id` is its flow — the same identity its
log line, its span and its error report carry. `context` is what the flow carried (the keys that
travel). A failure adds `escaped_from`, the bus whose caller received it.

**A failure says what to do about it.** `kind` is the failure's `FailureKind` — the classification
every wire answers its caller with (`common.failures`); `retry_after` is how long to wait before
sending the same call again, `None` when sending it again will not help.

**Who hears them.** Every subscriber of the process, and the subscribers of the bus the execution ran
in — once each. A subscriber is a function; one that raises is logged and never reaches the
execution, nor the next subscriber. Delivery to a queue is a `Publisher` like any other: in process,
in the background, over a broker. With nobody listening, nothing is built.

**Hearing an outcome is never announced.** A bus that executes an `ExecutionCompleted` or an
`ExecutionFailed` announces nothing for it — a listener handing outcomes to a bus would otherwise
hear its own hearing without end; a failure there is logged.

**Announced when the use case answered, not when a transaction commits.** A completion inside a unit
of work that a caller rolls back afterwards was still announced; what must follow the commit is
recorded through the context's event table and its relay.
"""

import json
from typing import Any

from sincpro_framework.bus_pipeline.outcomes.events import (
    FAILURE_ID,
    OUTCOME_EVENTS,
    ExecutionCompleted,
    ExecutionFailed,
)
from sincpro_framework.bus_pipeline.outcomes.listeners import (
    OutcomeListener,
    completions,
    failures,
)
from sincpro_framework.bus_pipeline.outcomes.mixins.announcer import FAILED_EXECUTION
from sincpro_framework.common.naming import registered_name
from sincpro_framework.context.domain.execution import Execution
from sincpro_framework.context.domain.keys import travelling
from sincpro_framework.context.infrastructure.tree import current
from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.sincpro_logger import logger


def _as_json(value: Any) -> Any:
    """A DTO, or what a handler returned, as JSON values — described when it cannot be written."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    try:
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        if hasattr(value, "as_json"):
            return json.loads(value.as_json())
        return json.loads(json.dumps(value))
    except (
        Exception
    ):  # noqa: BLE001 - a value that cannot be written is described, not raised
        pass
    return {"repr": repr(value)}


def _carried() -> dict[str, Any]:
    return {
        key: value
        for key, value in travelling(current().flattened()).items()
        if key not in ("execution_id", "causation_id")
    }


def _announced(event: DomainEvent, listeners: list[OutcomeListener]) -> None:
    for listener in listeners:
        try:
            listener(event)
        except Exception:  # noqa: BLE001 - a listener never reaches the execution
            logger.exception(
                f"a listener of {event.name} failed: "
                f"{getattr(listener, '__qualname__', listener)!r}"
            )


def announce_completion(
    dto: Any, response: Any, bus: str, level: str, execution: Execution | None
) -> None:
    """1. An outcome heard is never announced again.
    2. The event: the DTO and the response as JSON values, the execution, the flow's context.
    3. Final: each listener of `bus`, in turn, never raising."""
    if isinstance(dto, OUTCOME_EVENTS):
        return
    event = ExecutionCompleted(
        use_case=registered_name(type(dto), bus),
        bus=bus,
        level=level,
        dto=_as_json(dto),
        response=_as_json(response),
        response_type=type(response).__name__ if response is not None else "",
        execution_id=execution.execution_id if execution is not None else "",
        causation_id=execution.execution_id if execution is not None else None,
        correlation_id=execution.correlation_id if execution is not None else None,
        entity_type="Execution",
        entity_id=execution.execution_id if execution is not None else "",
        context=_carried(),
    )
    _announced(event, completions.listening(bus))


def _classified(error: BaseException) -> tuple[str, float | None]:
    """The kind every wire answers `error` with, and the seconds to wait before a retry."""
    from sincpro_framework.common.failures import refined_failure_kind, retry_after
    from sincpro_framework.exceptions import FailureKind

    if not isinstance(error, Exception):
        return FailureKind.INTERNAL.value, None
    kind = refined_failure_kind(error)
    wait = retry_after(error, kind)
    return kind.value, wait.total_seconds() if wait is not None else None


def announce_failure(
    error: BaseException, dto: Any, bus: str, escaped_from: str, origin: Any
) -> None:
    """1. An outcome heard is never announced again.
    2. The event: where it happened (`origin`, the record observability kept), the execution that
       failed (left on the exception by its bus), its kind and wait as every wire answers it, the
       flow's context.
    3. Final: each listener, in turn, never raising; the exception carries the event's id."""
    failed = getattr(error, FAILED_EXECUTION, None)
    failed_dto = getattr(origin, "dto", dto)
    if isinstance(failed_dto, OUTCOME_EVENTS) or isinstance(dto, OUTCOME_EVENTS):
        return
    kind, wait = _classified(error)
    event = ExecutionFailed(
        use_case=registered_name(type(failed_dto), bus),
        bus=bus,
        level=getattr(origin, "layer", "") or "",
        escaped_from=escaped_from,
        dto=_as_json(failed_dto),
        error_type=type(error).__name__,
        error=str(error),
        kind=kind,
        retry_after=wait,
        execution_id=failed.execution_id if failed is not None else "",
        causation_id=failed.execution_id if failed is not None else None,
        correlation_id=failed.correlation_id if failed is not None else None,
        entity_type="Execution",
        entity_id=failed.execution_id if failed is not None else "",
        context=_carried(),
    )
    try:
        setattr(error, FAILURE_ID, event.id)
    except (AttributeError, TypeError):
        pass
    _announced(event, failures.listening(bus))
