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
every wire answers its caller with (`transport.failures`); `retry_after` is how long to wait before
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
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sincpro_framework.bus import FAILED_EXECUTION
from sincpro_framework.context.domain.execution import Execution
from sincpro_framework.context.domain.keys import travelling
from sincpro_framework.context.infrastructure.tree import current
from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.sincpro_logger import logger

FAILURE_ID = "failure_id"
"""What an escaped exception carries: the id of the `ExecutionFailed` that went out with it."""

type OutcomeListener = Callable[[Any], Any]


@dataclass(kw_only=True)
class ExecutionCompleted(DomainEvent):
    """A use case that answered — what it was handed, what it gave, where, in which flow."""

    name = "sincpro.execution.v1.completed"

    use_case: str = ""
    """The DTO that was executed."""
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
    """What the flow carried — the keys that travel; never a `Secret`."""


@dataclass(kw_only=True)
class ExecutionFailed(DomainEvent):
    """An exception that escaped a call — what failed, where, in which flow, and why."""

    name = "sincpro.execution.v1.failed"

    use_case: str = ""
    """The DTO whose execution raised."""
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
    """What the flow carried — the keys that travel; never a `Secret`."""


OUTCOMES = (ExecutionCompleted, ExecutionFailed)
"""What hearing an outcome produces is never announced: a listener that hands outcomes to a bus
would otherwise hear the outcome of its own hearing, without end."""


class Listeners:
    """Who hears one outcome in this process: its own subscribers, and each bus's."""

    def __init__(self) -> None:
        self._process: list[OutcomeListener] = []
        self._by_bus: dict[str, list[OutcomeListener]] = {}
        self._lock = threading.Lock()

    def subscribe(self, listener: OutcomeListener, bus: str | None = None) -> OutcomeListener:
        """`listener` hears every outcome of the process — or of `bus` only. Usable as a
        decorator."""
        with self._lock:
            if bus is None:
                self._process.append(listener)
            else:
                self._by_bus.setdefault(bus, []).append(listener)
        return listener

    def unsubscribe(self, listener: OutcomeListener) -> None:
        with self._lock:
            if listener in self._process:
                self._process.remove(listener)
            for listeners in self._by_bus.values():
                if listener in listeners:
                    listeners.remove(listener)

    def heard(self, bus: str) -> bool:
        """Whether anybody hears an outcome of `bus` — read without the lock: the cost of every
        execution nobody listens to."""
        return bool(self._process) or bool(self._by_bus.get(bus))

    def listening(self, bus: str) -> list[OutcomeListener]:
        """Who hears an outcome of `bus` — each once, the process's first."""
        with self._lock:
            heard = [*self._process, *self._by_bus.get(bus, [])]
        unique: list[OutcomeListener] = []
        for listener in heard:
            if listener not in unique:
                unique.append(listener)
        return unique

    def clear(self) -> None:
        """Nobody hears anything — what a test starts from."""
        with self._lock:
            self._process.clear()
            self._by_bus.clear()


completions = Listeners()
"""The completions of this process."""

failures = Listeners()
"""The failures of this process."""


def publishing(publisher: Any) -> OutcomeListener:
    """A listener that hands each outcome to `publisher` — a queue, another bus, a broker."""

    def publish(outcome: DomainEvent) -> None:
        publisher.publish(outcome)

    publish.__qualname__ = f"publish_to({type(publisher).__name__})"
    return publish


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


def completed(
    dto: Any, response: Any, bus: str, level: str, execution: Execution | None
) -> None:
    """1. Who hears a completion of `bus` — nobody, and nothing is built.
    2. The event: the DTO and the response as JSON values, the execution, the flow's context.
    3. Final: each listener, in turn, never raising."""
    if not completions.heard(bus) or isinstance(dto, OUTCOMES):
        return
    event = ExecutionCompleted(
        use_case=type(dto).__name__,
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
    from sincpro_framework.transport.failures import (
        FailureKind,
        refined_failure_kind,
        retry_after,
    )

    if not isinstance(error, Exception):
        return FailureKind.INTERNAL.value, None
    kind = refined_failure_kind(error)
    wait = retry_after(error, kind)
    return kind.value, wait.total_seconds() if wait is not None else None


def escaped(error: BaseException, dto: Any, escaped_from: str, origin: Any) -> None:
    """1. Who hears a failure of the bus it happened in — nobody, and nothing is built.
    2. The event: where it happened (`origin`, the record observability kept), the execution that
       failed (left on the exception by its bus), its kind and wait as every wire answers it, the
       flow's context.
    3. Final: each listener, in turn, never raising; the exception carries the event's id."""
    bus = getattr(origin, "bus", "") or escaped_from
    if not failures.heard(bus):
        return
    failed = getattr(error, FAILED_EXECUTION, None)
    failed_dto = getattr(origin, "dto", dto)
    if isinstance(failed_dto, OUTCOMES) or isinstance(dto, OUTCOMES):
        return
    kind, wait = _classified(error)
    event = ExecutionFailed(
        use_case=type(failed_dto).__name__,
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
