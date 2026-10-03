# Outcomes: what a call did, as events anyone may listen to

Every use case that answers says so with an `ExecutionCompleted` — the DTO it was handed, the response
it gave. When an exception leaves a call, an `ExecutionFailed` goes out with it. Alerts, an audit, a
projection in another service, whatever undoes a step: each listens, none is written into a Feature.

Every block on this page runs, in order, in `tests/docs/test_persistence_guide.py`.

## It goes with the exception

```python
import pytest

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.outcomes import ExecutionFailed, failures


class CommandIssueInvoice(DataTransferObject):
    order_id: str


class SiatUnavailable(Exception):
    pass


billing = UseFramework("billing-failures", log_after_execution=False)
heard: list[ExecutionFailed] = []


@billing.feature(CommandIssueInvoice)
class IssueInvoice(Feature):
    def execute(self, dto: CommandIssueInvoice) -> None:
        raise SiatUnavailable("SIAT is not answering")


@billing.on_failure                                   # every failure of this bus that escapes
def alert(failure: ExecutionFailed) -> None:
    heard.append(failure)


with billing.context({"tenant_id": "acme"}):
    with pytest.raises(SiatUnavailable) as raised:
        billing(CommandIssueInvoice(order_id="O-7"))

[failure] = heard
assert (failure.use_case, failure.error_type) == ("CommandIssueInvoice", "SiatUnavailable")
assert failure.dto == {"order_id": "O-7"} and failure.context["tenant_id"] == "acme"
assert (failure.kind, failure.retry_after) == ("internal", None)   # what every wire answers
assert raised.value.failure_id == failure.id          # the exception names the event
```

| What happened | Event |
|---|---|
| A use case raised and nothing contained it — the caller received it | **one** |
| An error handler answered instead of raising | **none** — whoever contained it decided it does not travel |
| An error handler logged and raised again | one — it still escaped |
| A handler's own `try/except` caught it | none — it never escaped |
| It failed deep down (Feature → ApplicationService → another bus) | **one**, where it left the whole call |
| It failed in another service (`remote_execution`) | one there; one here too when it escapes here, joined by `causation_id` |

## What it says

| Field | Meaning |
|---|---|
| `use_case`, `bus`, `level` | the use case that raised, its bounded context, `feature` or `application_service` |
| `escaped_from` | the bus whose caller received the exception |
| `dto` | the DTO as it was handed, as JSON values |
| `error_type`, `error` | the exception's class and message |
| `kind` | the `FailureKind` every wire answers it with (`transport.failures`): `domain`, `conflict`, `unavailable`, `internal`… |
| `retry_after` | seconds to wait before sending the same call again — `None` when sending it again will not help |
| `execution_id` · `causation_id` · `correlation_id` | the execution that failed, and its flow — what its log line, span and error report carry |
| `context` | what the flow carried — the keys that travel, never a `Secret` |

It is a `DomainEvent` (`sincpro.execution.v1.failed`): it is published, kept and carried like any
other.

A listener that retries or compensates decides with what the caller was told. An error declares its
own kind:

```python
from sincpro_framework.transport.failures import FailureKind


class CommandSendInvoice(DataTransferObject):
    invoice_id: str


class SiatDown(Exception):
    failure_kind = FailureKind.UNAVAILABLE                # a later retry may succeed
    retry_after = 30                                      # seconds; one when it says nothing


siat = UseFramework("siat-failures", log_after_execution=False)
retries: list[ExecutionFailed] = []
siat.on_failure(retries.append)


@siat.feature(CommandSendInvoice)
class SendInvoice(Feature):
    def execute(self, dto: CommandSendInvoice) -> None:
        raise SiatDown("SIAT is down")


with pytest.raises(SiatDown):
    siat(CommandSendInvoice(invoice_id="F-1"))

assert (retries[0].kind, retries[0].retry_after) == ("unavailable", 30.0)
```

## Who hears it

```python
from sincpro_framework.event_driven import Publisher, Subscriber, SyncQueue

ops = UseFramework("ops-failures", log_after_execution=False)
incidents: list[ExecutionFailed] = []


@ops.feature(ExecutionFailed)                          # another bus, as any event
class OpenIncident(Feature):
    def execute(self, dto: ExecutionFailed) -> None:
        incidents.append(dto)


billing.publish_failures(to=Publisher(SyncQueue(Subscriber(ops))))
everything: list[ExecutionFailed] = []
failures.subscribe(everything.append)                  # every bus of the process

with pytest.raises(SiatUnavailable):
    billing(CommandIssueInvoice(order_id="O-8"))

assert incidents[0].dto == {"order_id": "O-8"} and len(everything) == 1
failures.clear()
```

- `@bus.on_failure` — a function, for the failures of one bus.
- `failures.subscribe(fn)` — every bus of the process.
- `bus.publish_failures(to=publisher)` — a queue: another bus (`SyncQueue`), the background
  (`BackgroundQueue`), a broker topic — the error queue (`FastStreamQueue`).

A failure is heard by the subscribers of the process and of the bus it **failed in**, each once. A
listener that raises is logged; it never reaches the failed execution, its caller, or the next
listener. With nobody listening, nothing is built.

## Every use case that answered

```python
from sincpro_framework.outcomes import ExecutionCompleted, completions


class CommandReserveStock(DataTransferObject):
    order_id: str


class ResponseReserveStock(DataTransferObject):
    reservation_id: str


warehouse = UseFramework("warehouse-outcomes", log_after_execution=False)
done: list[ExecutionCompleted] = []


@warehouse.feature(CommandReserveStock)
class ReserveStock(Feature):
    def execute(self, dto: CommandReserveStock) -> ResponseReserveStock:
        return ResponseReserveStock(reservation_id=f"R-{dto.order_id}")


@warehouse.on_completion                              # every use case of this bus that answered
def heard(completed: ExecutionCompleted) -> None:
    done.append(completed)


with warehouse.context({"tenant_id": "acme"}):
    warehouse(CommandReserveStock(order_id="O-7"))

[completed] = done
assert completed.use_case == "CommandReserveStock" and completed.dto == {"order_id": "O-7"}
assert completed.response == {"reservation_id": "R-O-7"}
assert completed.response_type == "ResponseReserveStock" and completed.context["tenant_id"] == "acme"
```

| What happened | Event |
|---|---|
| A Feature or an ApplicationService returned | **one** each — the Features an ApplicationService runs too, each its own |
| An error handler answered instead | **none** — the use case did not complete |
| It raised | an `ExecutionFailed`, when it escapes |

| Field | Meaning |
|---|---|
| `use_case`, `bus`, `level` | the use case, its bounded context, `feature` or `application_service` |
| `dto` · `response` · `response_type` | what it was handed and what it returned, as JSON values (`None` when it returned nothing) |
| `execution_id` · `causation_id` · `correlation_id` | the execution, and its flow |
| `context` | what the flow carried — the keys that travel |

It is a `DomainEvent` (`sincpro.execution.v1.completed`), heard the same three ways as a failure:
`@bus.on_completion`, `completions.subscribe(fn)` for every bus of the process,
`bus.publish_completions(to=publisher)` for a queue. With nobody listening, nothing is built.

```python
audit = UseFramework("audit-outcomes", log_after_execution=False)
kept: list[str] = []


@audit.feature(ExecutionCompleted)                     # another bus, as any event
class KeepTrail(Feature):
    def execute(self, dto: ExecutionCompleted) -> None:
        kept.append(f"{dto.bus}:{dto.use_case}")


warehouse.publish_completions(to=Publisher(SyncQueue(Subscriber(audit))))
warehouse(CommandReserveStock(order_id="O-8"))

assert kept == ["warehouse-outcomes:CommandReserveStock"]   # audit's own run is not announced
completions.clear()
```

A bus that executes an `ExecutionCompleted` or an `ExecutionFailed` announces nothing for it: a
listener handing outcomes to a bus would otherwise hear its own hearing without end.

**Announced when the use case answered**, not when a transaction commits: a completion inside a unit
of work that its caller rolls back afterwards was still announced. What must follow the commit goes
through the context's event table and its relay.
