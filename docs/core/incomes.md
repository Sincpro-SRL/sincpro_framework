# Incomes: what a use case was handed, as an event anyone may listen to

Every use case that begins says so with an `ExecutionStarted` — the DTO it was handed, before
anything runs. How it ends is its [outcomes](outcomes.md): the same execution, told by its
`execution_id`. An audit trail that opens before the work, a log of what is asked, a projection in
another service: each listens, none is written into a Feature.

An income **observes**. It never changes what the call answers: that is an
[interceptor](interceptors.md)'s. See [the execution path](execution-path.md).

Every block on this page runs, in order, in `tests/docs/test_persistence_guide.py`.

## A use case that begins

```python
from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.bus_pipeline.incomes.events import ExecutionStarted
from sincpro_framework.bus_pipeline.incomes.listeners import start_listeners
from sincpro_framework.bus_pipeline.outcomes.events import ExecutionCompleted


class CommandReserveStock(DataTransferObject):
    order_id: str


class ResponseReserveStock(DataTransferObject):
    reservation_id: str


warehouse = UseFramework("warehouse-incomes", log_after_execution=False)
began: list[ExecutionStarted] = []
ended: list[ExecutionCompleted] = []


@warehouse.feature(CommandReserveStock)
class ReserveStock(Feature):
    def execute(self, dto: CommandReserveStock) -> ResponseReserveStock:
        return ResponseReserveStock(reservation_id=f"R-{dto.order_id}")


@warehouse.on_start                                   # every use case of this bus that begins
def opened(started: ExecutionStarted) -> None:
    began.append(started)


warehouse.on_completion(ended.append)

with warehouse.context({"tenant_id": "acme"}):
    warehouse(CommandReserveStock(order_id="O-7"))

[started] = began
assert started.use_case == "warehouse-incomes.CommandReserveStock"
assert started.dto == {"order_id": "O-7"}
assert started.context["tenant_id"] == "acme"
assert started.execution_id == ended[0].execution_id    # the outcome of the same execution
```

| What happened | Event |
|---|---|
| A Feature or an ApplicationService begins | **one** each — the Features an ApplicationService runs too, each its own |
| It never gets to run (a `requires_context` that is not met) | **none** |
| It then answers, or raises | its `ExecutionCompleted`, or the `ExecutionFailed` when it escapes — with the same `execution_id` |

| Field | Meaning |
|---|---|
| `use_case`, `bus`, `level` | the use case by its identity (`context.Class`), its bounded context, `feature` or `application_service` |
| `dto` | what it was handed, as JSON values |
| `execution_id` · `causation_id` · `correlation_id` | the execution — the one its completion or failure names — and its flow |
| `context` | what the flow carried when it began — the keys that travel |

It is a `DomainEvent` (`sincpro.execution.v1.started`), so a bus or a queue receives it like any
event; it is announced to listeners and written nowhere.

## Where it stands: before the interceptors

An income is announced once the context providers have run — so `context` holds what they derived —
and **before the interceptors**: it is the call as its caller made it, not as an interceptor
adjusted it. The outcome is announced after them, and says what the caller received.

```python
from typing import Any

shop = UseFramework("shop-incomes", log_after_execution=False)
handled: list[str] = []
opened_with: list[ExecutionStarted] = []


@shop.feature(CommandReserveStock)
class Reserve(Feature):
    def execute(self, dto: CommandReserveStock) -> None:
        handled.append(dto.order_id)


@shop.interceptor(CommandReserveStock)
def adjust(dto: CommandReserveStock, call_next: Any) -> Any:
    return call_next(dto.model_copy(update={"order_id": "ADJUSTED"}))


shop.on_start(opened_with.append)
shop(CommandReserveStock(order_id="O-7"))

assert opened_with[0].dto == {"order_id": "O-7"}        # as it was handed
assert handled == ["ADJUSTED"]                          # as the handler received it
```

| An interceptor that… | The income | The outcome |
|---|---|---|
| adjusts the DTO | the DTO as handed | the response the caller got |
| answers by itself (a cache) | one | one, with the cached response |
| vetoes | one | an `ExecutionFailed`, when it escapes |

## Who hears it

```python
from sincpro_framework.event_driven import Publisher, Subscriber, SyncQueue

ops = UseFramework("ops-incomes", log_after_execution=False)
trail: list[str] = []


@ops.feature(ExecutionStarted)                         # another bus, as any event
class RecordTrail(Feature):
    def execute(self, dto: ExecutionStarted) -> None:
        trail.append(dto.use_case)


warehouse.publish_starts(to=Publisher(SyncQueue(Subscriber(ops))))
everything: list[ExecutionStarted] = []
start_listeners.subscribe(everything.append)                    # every bus of the process

warehouse(CommandReserveStock(order_id="O-8"))

assert trail == ["warehouse-incomes.CommandReserveStock"]   # ops' own run is not announced
assert len(everything) == 1
start_listeners.clear()
```

- `@bus.on_start` — a function, for the starts of one bus.
- `start_listeners.subscribe(fn)` — every bus of the process.
- `bus.publish_starts(to=publisher)` — a queue: another bus (`SyncQueue`), the background
  (`BackgroundQueue`), a broker topic (`FastStreamQueue`).

A start is heard by the subscribers of the process and of its bus, each once, in the order they were
added. A listener that raises is logged; it never reaches the execution, its caller, or the next
listener. A listener may be added at any time, and a generation of the bus (`fresh`) hears what the
bus does. With nobody listening, nothing is built.

A bus that executes an `ExecutionStarted` announces nothing for it: a listener handing starts to a
bus would otherwise hear its own hearing without end.
