# Recording and publishing

## The aggregate records

```python
from dataclasses import dataclass
from sincpro_framework.ddd import DomainEvent, Entity
from sincpro_framework.event_driven import Publisher, Subscriber, SyncQueue


@dataclass(kw_only=True)
class InvoicePosted(DomainEvent):
    name = "billing.invoice.v1.posted"     # the wire name; keep it stable
    invoice_id: str = ""
    total: int = 0


@dataclass
class Invoice(Entity):
    ...

    def post(self) -> None:                # the rule lives on the aggregate it changes
        self.state = "posted"
        self.record(InvoicePosted(invoice_id=self.id, total=self.total))
```

- `@dataclass(kw_only=True)` is required on every event class: without it the annotations are not
  fields and the constructor refuses them.
- `name` is a plain assignment. A typed `name: str` is refused when the class is declared. A
  subclass that sets no `name` gets its own class name, never its parent's.
- `record` stores a **stamped copy** (the aggregate's type and id) and
  returns it; the instance you passed is left as it was. `pull_events()` hands the recorded events
  back **and forgets them** — pull once, after the save. `recorded_events()` looks without taking.
- With `map_events` on the context's base event, `save(aggregate)` keeps these facts in the
  same transaction without pulling them. Plain `record` does not assign a stream version;
  `EventSourcedMixin.happened` assigns `entity_version` and applies the fact.
- For durable delivery, mark the event `DeliverableEventMixin` and let `EventRelay` send it.
  Do not also publish it in the manual loop below.

```python
draft = repository.get_by(Invoice, number="F-004")
draft.post()
repository.save(draft)
for event in draft.pull_events():
    publisher.publish(event)
```

## The subscriber is a Feature

```python
notifications = UseFramework("notifications")

@notifications.feature(InvoicePosted)      # a list registers several DTOs on one algorithm
class EmailTheCustomer(Feature):
    def execute(self, dto: InvoicePosted) -> None:
        ...                                # send
```

`Subscriber(notifications)` executes every bus whose registry knows the event's `name`. A bus
declares nothing extra: an event is a DTO, so `@bus.feature(SomeEvent)` **is** the subscription.
For several events on one bus, `@bus.feature([Command1, EventA, EventB])` — only when they share
one algorithm. An event no bus registered is not an error: `publish` returns and nothing ran.

## Publisher: the signature of a bus

```python
publisher.publish(event)                     →  None          # launch and forget
publisher.publish(event, ResponseDTO)        →  ResponseDTO   # wait for the answer
await publisher.get_async_publisher().publish(event, ResponseDTO)
```

The typed form holds only where somebody can answer **in the same call**: a `SyncQueue` with exactly
one bus for that event. A `BackgroundQueue` cannot answer from another process, and two buses have
no one answer — both are refused rather than guessed. What a bus raises, the publisher raises.
Handing it a Command (any non-`DomainEvent`) is refused: a Command is executed on its bus.

`subscriber.handle(event)` / `subscriber.get_async_subscriber().handle(event)` are the two forms on
the receiving side.

## Wiring: the publisher is a dependency

The Feature reads `self.publisher`, registered like any adapter:

```python
# domains/common/infrastructure/events.py — no context imported here
BUSES: dict[str, UseFramework] = {}
PUBLISHER = Publisher(SyncQueue(lambda: Subscriber(*BUSES.values())))

# domains/billing/infrastructure/framework.py
def config_billing_framework(name: str) -> UseFramework[BillingDependencies]:
    instance = UseFramework[BillingDependencies](name)
    instance.add_dependency("publisher", PUBLISHER)
    register_dependencies(instance)
    BUSES[name] = instance
    return instance
```

A queue needs a subscriber, a subscriber needs the buses, and a bus needs the publisher the queue is
behind. Handed a function, `SyncQueue` asks for nothing until somebody publishes — by then every
bus is registered — and keeps the subscriber it built. `add_dependency` is refused once the bus is
built, so register the publisher before the first execution.

## The two queues

**`SyncQueue(subscriber)`** — `publish` runs the buses where it was called and returns. For one
process, development, tests, and reactions that must be visible at once. **Publishing runs the
subscriber in the call**, so a subscriber that publishes re-enters the queue depth-first. Where
order of facts matters, write the fact down in the context it happened in, or send it through an
outbox whose relay delivers in order.

**`BackgroundQueue(build_subscriber)`** — the smallest "do it in the background":

```python
def build_subscriber() -> Subscriber:      # a module-level function: it runs in the worker
    return Subscriber(planning, assurance)

queue = BackgroundQueue(build_subscriber).start()
Publisher(queue).publish(event)            # into a multiprocessing.Queue, and back at once
queue.stop()
```

- The worker is a *spawned* interpreter (`fork` is refused) that builds its own buses; a built
  `Subscriber` or a lambda cannot be sent to it.
- The event crosses as its **wire name** and JSON, and is rebuilt as the class the subscriber knows.
  An event no bus there registered is skipped; a failing handler is logged and reported in the
  worker, and the worker continues.
- `stop()` sends the stop signal and waits. A queue never started refuses a publish.
- It carries the **trace** as a W3C carrier beside the payload, so the consumer lands under the
  span that published.
- Events still in the queue when the process stops are lost: at most once.

## Testing what was published

```python
from sincpro_framework.testing import RecordingQueue, override_dependencies

published = RecordingQueue()                              # or RecordingQueue(SyncQueue(...)) to also deliver
with override_dependencies(billing, publisher=Publisher(published)):
    billing(CommandPostInvoice(invoice_id="F-1"))
assert [one.invoice_id for one in published.of(InvoicePosted)] == ["F-1"]
```

## Two identities, one travels

`name` is what the event is called on the wire; the Python class is what it is here. A context that
may not import the publisher's module declares its own class under the same `name`, and the
subscriber hands each bus the class **that bus registered**. Sharing one class is simpler and cannot
drift — prefer it.
