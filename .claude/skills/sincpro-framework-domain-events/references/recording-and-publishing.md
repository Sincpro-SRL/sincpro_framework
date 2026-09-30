# Recording and publishing

## The aggregate records

```python
from dataclasses import dataclass
from sincpro_framework.ddd import DomainEvent


@dataclass(kw_only=True)
class InvoicePosted(DomainEvent):
    name = "billing.invoice.v1.posted"     # the wire name; keep it stable
    invoice_id: str = ""
    total: int = 0


def post(invoice: Invoice) -> None:
    invoice.state = "posted"
    invoice.record(InvoicePosted(invoice_id=invoice.id, total=invoice.total))
```

`record` stamps the aggregate's type, id and sequence onto the event. `pull_events()` hands them
back **and forgets them** — pull once, after the save.

```python
draft = repository.get_by(Invoice, number="F-004")
post(draft)
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

`Subscriber(notifications)` executes every bus whose registry knows the event. A bus declares
nothing extra: an event is a DTO, so `@bus.feature(SomeEvent)` **is** the subscription. For several
events on one bus, `@bus.feature([Command1, EventA, EventB])` — only when they share one algorithm.

## Publisher: the signature of a bus

```python
publisher.publish(event)                     →  None          # launch and forget
publisher.publish(event, ResponseDTO)        →  ResponseDTO   # wait for the answer
await publisher.get_async_publisher().publish(event, ResponseDTO)
```

The typed form holds only where somebody can answer **in the same call**: a `SyncQueue` with exactly
one bus for that event. A `BackgroundQueue` cannot answer from another process, and two buses have
no one answer — both are refused rather than guessed. What a bus raises, the publisher raises.

`subscriber.handle(event)` / `subscriber.get_async_subscriber().handle(event)` are the two forms on
the receiving side.

## The two queues

**`SyncQueue(subscriber)`** — `publish` runs the buses where it was called and returns. For
development, tests, and reactions that must be visible at once. It also takes a **function**, which
is what a composition root wants, because it cuts the wiring circle:

```python
BUSES: dict[str, UseFramework] = {}
publisher = Publisher(SyncQueue(lambda: Subscriber(*BUSES.values())))   # before any bus exists
```

A queue needs a subscriber, a subscriber needs the buses, and a bus needs the publisher the queue is
behind. Handed a function, the queue asks for nothing until somebody publishes, and by then every
bus is registered.

**Publishing runs the subscriber in the call**, so a subscriber that publishes re-enters the queue
depth-first. Where order of facts matters, write the fact down in the context it happened in, or
send it through an outbox whose relay delivers in order.

**`BackgroundQueue(build_subscriber)`** — the smallest "do it in the background":

```python
def build_subscriber() -> Subscriber:      # a module-level function: it runs in the worker
    return Subscriber(planning, assurance)

queue = BackgroundQueue(build_subscriber).start()
Publisher(queue).publish(event)            # into a multiprocessing.Queue, and back at once
queue.stop()
```

The worker builds its own buses (a bus does not cross a process). The event crosses as its **wire
name** and JSON, and is rebuilt as the class the subscriber knows. `stop()` sends the stop signal
and waits. A queue never started refuses a publish rather than dropping it.

`BackgroundQueue` also carries the **trace** as a W3C carrier beside the payload; the worker adopts
it, so the consumer lands under the span that published.

## Two identities, one travels

`name` is what the event is called on the wire; the Python class is what it is here. A context that
may not import the publisher's module declares its own class under the same `name`, and the
subscriber hands each bus the class **that bus registered**. Sharing one class is simpler and cannot
drift — prefer it.
