"""Outcomes: how a use case ended, announced as an event anyone may listen to.

    @billing.on_completion                               a function, in this process
    def done(completed: ExecutionCompleted) -> None: ...
    @billing.on_failure
    def alert(failure: ExecutionFailed) -> None: ...

    billing.publish_completions(to=Publisher(queue))     a queue: another bus, a broker topic
    billing.publish_failures(to=Publisher(queue))
    completion_listeners.subscribe(audit) · failure_listeners.subscribe(audit)     every bus of the process

    try:
        billing(CommandIssueInvoice(...))
    except Exception as error:
        error.failure_id                                 the event that went out with it

How a use case began is the income's (`sincpro_framework.bus_pipeline.incomes`).

**A completion is every use case that answered.** Each Feature and ApplicationService whose handler
returned — the Features an ApplicationService runs included, each its own — with the DTO it was
handed and the response it gave. It is announced after the interceptors, so it says what the caller
received. One an error handler answered for did not complete: no event.

**A failure goes with the exception.** It is emitted when an exception escapes the whole call — once,
where the caller receives it — and never when an error handler answered instead: whoever contained
the error decided it does not travel. A failure caught by a handler's own `try` never escaped either.

**Each says where and in which flow.** `use_case`, `bus` and `level` name the use case; `execution_id`
is the execution, `causation_id` names it too, `correlation_id` is its flow — the same identity its
log line, its span and its error report carry. `context` is what the flow carried (the keys that
travel). A failure adds `escaped_from`, the bus whose caller received it, and names the use case
where it first failed: its DTO, its bus and its execution.

**A failure says what to do about it.** `kind` is the failure's `FailureKind` — the classification
every wire answers its caller with (`common.failures`); `retry_after` is how long to wait before
sending the same call again, `None` when sending it again will not help.

**Who hears them.** Every subscriber of the process, and the subscribers of the bus the execution ran
in — a failure, the ones of the bus it *failed* in — once each. A subscriber is a function; one that
raises is logged and never reaches the execution, nor the next subscriber. Delivery to a queue is a
`Publisher` like any other: in process, in the background, over a broker. With nobody listening,
nothing is built.

**Hearing an outcome is never announced.** A bus that executes an execution event announces nothing
for it — a listener handing them to a bus would otherwise hear its own hearing without end; a failure
there is logged.

**Announced when the use case answered, not when a transaction commits.** A completion inside a unit
of work that a caller rolls back afterwards was still announced; what must follow the commit is
recorded through the context's event table and its relay.

Context: the events load with the first who asks for them — a process nobody listens in never loads
them, nor the DDD layer they are built on.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sincpro_framework.bus_pipeline.outcomes.events import (
        ExecutionCompleted,
        ExecutionFailed,
    )


def __getattr__(name: str) -> Any:
    """`ExecutionCompleted` and `ExecutionFailed`, imported at the first who asks for them —
    so a process nobody listens in never loads the events nor the DDD layer under them."""
    if name in ("ExecutionCompleted", "ExecutionFailed"):
        from sincpro_framework.bus_pipeline.outcomes import events

        return getattr(events, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["ExecutionCompleted", "ExecutionFailed"]
