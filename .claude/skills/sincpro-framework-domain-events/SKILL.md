---
name: sincpro-framework-domain-events
description: Record, publish and store domain events with sincpro_framework — DomainEvent, Entity.record/pull_events, Publisher, SyncQueue/BackgroundQueue, change tracking, event sourcing and the transactional outbox, plus Kafka/RabbitMQ/Redis/NATS through FastStream. Use whenever the task says "when X happens, notify/emit/react", publishes an event, subscribes one context to another, builds an audit trail, an outbox/relay, an event store, or wires a broker in a Sincpro Python service.
---

# sincpro-framework-domain-events

A domain event is a fact one bounded context states after its state changed, so that other
contexts can react without being called. Nothing is wired by default and nothing is stored: a
project builds the queue it wants over the buses it names, and a Feature publishes when it decides
to. What an aggregate recorded lives in memory and dies with the object.

## Context

- **Solves:** "when X happens in context A, B and C react" without A importing or calling B and C;
  an audit trail of what changed; facts that cross a process or a database.
- **Is not:** a way to ask for work. A Command is asked of one bus directly (`bus(command)`);
  `Publisher.publish` refuses anything that is not a `DomainEvent`. It is not a Command consumer
  from a broker either — that is `QueueGateway` (`sincpro-framework-entrypoints`).
- **Do not use it** to call a use case in your own context (execute its Command), to get a value
  back from another context (execute that context's Query on its bus), or to share state between
  two Features of one bus.
- **Pick the wiring from the system shape:** one database → `SyncQueue` is enough; a database per
  context, or a broker between processes → an outbox and a relay; the facts are the state → event
  sourcing.

## Abstractions

| Term | What it is | Kind | Import |
|---|---|---|---|
| `DomainEvent` | A past-tense fact: an `Entity` with the envelope `id` (UUID v7), `created_at`, `entity_type`, `entity_id`, `correlation_id`, `causation_id`, `sequence`, `label`, and a wire `name` | DTO | `sincpro_framework.ddd` |
| `name` | The wire identity of an event class (`"billing.invoice.v1.posted"`), a plain class attribute; defaults to the class name. Routing, storage and brokers match on it | setting | (class attribute) |
| `Entity.record` / `pull_events` / `recorded_events` | The aggregate states a fact (stamped with its type, id, sequence) / the Feature takes them all and they are forgotten / look without taking | function | `sincpro_framework.ddd` (methods of `Entity`) |
| `DomainEvent.caused_by(cause)` | A copy with `causation_id`/`correlation_id` threaded from the incoming event | function | (method) |
| `ChangeTrackingMixin` | Aggregate mixin: one `EntityUpdated` recorded per save, with every field that changed | DTO | `sincpro_framework.ddd` |
| `EntityUpdated` | The event `ChangeTrackingMixin` records (`changes`, `field_labels`); subclass it for your own name | DTO | `sincpro_framework.ddd` |
| `EventTrackableMixin` / `EventStatus` | Event mixin adding delivery state (`status`, `attempts`, `error_message`, `acknowledged_at`, `failed_at`, `mark_*`) — what turns a stored event into an outbox row | DTO | `sincpro_framework.ddd` |
| `Publisher` / `AsyncPublisher` | What a Feature holds: `publish(event)` or `publish(event, Response)`, into a `Queue` | function | `sincpro_framework.events` |
| `Queue` | Protocol: `put(event)` / `aput(event)` | port (abstract) | `sincpro_framework.events` |
| `SyncQueue` | Runs the subscriber inside the `publish` call, same process | adapter | `sincpro_framework.events` |
| `BackgroundQueue` | A spawned worker process consumes from a `multiprocessing.Queue`; `start()`/`stop()` | adapter | `sincpro_framework.events` |
| `Subscriber` / `AsyncSubscriber` | The buses that hear events; executes every bus whose registry knows `event.name` | registry | `sincpro_framework.events` |
| `@bus.feature(SomeEvent)` | The subscription itself: an ordinary Feature registered for the event class | decorator | (`UseFramework`) |
| `RecordingQueue` | Test queue that keeps what was published (`.of(Event)`), optionally forwarding | adapter | `sincpro_framework.testing` |
| `FastStreamQueue` | A `Queue` that sends to Kafka/RabbitMQ/Redis/NATS — extra `[faststream]` | adapter | `sincpro_framework.events.faststream` |
| `subscribe(broker, subscriber)` | Consumer side: one subscription per channel of the events the buses registered, acknowledged manually | function | `sincpro_framework.events.faststream` |
| `keyed_by_entity` / `by_event_name` | Kafka message key = `entity_id` / channel = event name | function | `sincpro_framework.events.faststream` |
| `QueueOptions` | Consumer settlement: `inbox`, `max_attempts`, dead-letter suffix, `subscription_of` | setting | `sincpro_framework.entrypoints.faststream` |
| `event_columns()` | The envelope columns for a table that stores events | function | `sincpro_framework.orm` |
| `delivery_columns()` | Delivery columns whose names do **not** match `EventTrackableMixin` — do not use for an outbox | function | `sincpro_framework.orm` |
| Event log / event store | Not a class: an event class mapped to a table and saved with the existing `Repository` | pattern | — |
| Outbox / relay | Not a class: an `EventTrackableMixin` event saved in the state change's transaction, and a loop you write that claims, delivers and acknowledges | pattern | — |

Look-alikes: `Queue` (carries events) ≠ `QueueGateway` (consumes Commands). `ChangeTrackingMixin`
(on an aggregate, *records* `EntityUpdated`) ≠ `EventTrackableMixin` (on an event, *tracks its
delivery*). Event log (state + history) ≠ event sourcing (history *is* the state).

## Architecture

**Inside the framework.** `sincpro_framework/ddd/` holds the vocabulary (`DomainEvent`,
`EventTrackableMixin`, `Entity.record`, `ChangeTrackingMixin`). `sincpro_framework/events/` holds
`Publisher`, `Subscriber`, the `Queue` protocol and its two standard-library adapters
(`SyncQueue`, `BackgroundQueue`). Optional extras, never core dependencies:
`events/faststream/` (`[faststream]` plus the broker driver, e.g. `faststream[kafka]`), the event
tables in `orm/` (`[sqlalchemy]`), and a Redis inbox through `caching.adapters.redis` (`[redis]`).

**Inside a consumer service** (one `UseFramework` per bounded context, created in
`infrastructure/framework.py` before `services/` is imported):

```
my_service/
  domains/
    common/infrastructure/events.py  # BUSES = {}; PUBLISHER = Publisher(SyncQueue(lambda: Subscriber(*BUSES.values())))
    billing/
      __init__.py                    # billing = config_billing_framework("billing"); from . import services
      domain/invoice.py              # aggregate: invoice.record(InvoicePosted(...))
      domain/events.py               # @dataclass(kw_only=True) class InvoicePosted(DomainEvent): name = "billing.invoice.v1.posted"
      infrastructure/framework.py    # UseFramework("billing"); BUSES["billing"] = instance
      infrastructure/dependencies.py # add_dependency("publisher", PUBLISHER), the repository, the tables
      services/post_invoice.py       # Feature: save, pull_events, publish
    notifications/
      services/email_customer.py     # @notifications.feature(InvoicePosted) — the subscriber
  entrypoints/
    consumer.py                      # broker process: subscribe(broker, Subscriber(...)); FastStream(broker).run()
    outbox_relay.py                  # the relay loop (a cron or a long-running process)
```

`common/` imports no sibling: each context adds itself to `BUSES`, and the `SyncQueue` function
asks for the buses only on the first publish, when all of them exist.

**The flow of one fact:**

```
Feature.execute(command)
  aggregate.record(Event)        in memory on the aggregate (a stamped copy)
  repository.save(aggregate)     commit; ChangeTrackingMixin records EntityUpdated here
  aggregate.pull_events()        handed over once, then forgotten
  publisher.publish(event) ──► Queue.put(event)
      SyncQueue        → Subscriber.handle → each bus with event.name registered → its Feature, in this call
      BackgroundQueue  → multiprocessing.Queue → worker: build_subscriber().handle(event)
      FastStreamQueue  → broker channel (event.name) → subscribe(...) in the consumer → its buses
outbox: one unit of work saves the aggregate + the event row → relay claims PENDING → put → mark_acknowledged
```

## Mistakes an agent makes

- **Publishing to a bus nobody wired.** `publish` returns `None` and runs nothing when no bus in
  the `Subscriber` registered `event.name` — no error, no log. Check with
  `Subscriber(...).listeners(Event.name)` or a test on `RecordingQueue`.
- **Publishing the instance you built instead of what was recorded.** `record` stores a *stamped
  copy*; the object you passed keeps `entity_type=""`, `entity_id=""`. Publish what
  `pull_events()` returns.
- **Pulling before the save.** `ChangeTrackingMixin` records at save time; pulling first misses the
  `EntityUpdated`. Save, then pull, then publish.
- **Publishing inside the unit of work.** With `SyncQueue`, subscribers run inside `publish`; doing
  it inside `with repository.context():` announces a fact a rollback can undo.
- **Save-then-publish across processes.** A crash between commit and publish loses the event
  silently. When the fact leaves the process, use the outbox.
- **No explicit `name`.** The wire name defaults to the class name: renaming the class silently
  stops routing to consumers and stored rows. Set `name = "<context>.<aggregate>.v1.<verb>"`.
- **`delivery_columns()` for an outbox table.** It emits `failure`/`delivered_at`, the mixin writes
  `error_message`/`acknowledged_at`/`failed_at`: those are dropped without error. Declare the five
  columns (`references/outbox.md`).
- **Channel mismatch.** A producer using `channel_of=` and a consumer calling `subscribe` without
  the matching `channel_of_name=` never meet.
- **Two services on one event over RabbitMQ.** Both consume the queue named after the channel and
  *compete*: each sees part of the events. Use `QueueOptions(subscription_of=...)` with an exchange
  per group, or Kafka/NATS consumer groups.
- **Trusting `BackgroundQueue`, Redis Pub/Sub or core NATS with a fact that matters.** All are at
  most once; a `BackgroundQueue` failure is logged in the worker process only.

## The rule that shapes everything

**The aggregate records; the Feature that saved it publishes.** The aggregate never publishes
itself — a rollback would then undo a fact the world already heard.

```python
from dataclasses import dataclass

from sincpro_framework.ddd import DomainEvent


@dataclass(kw_only=True)                 # required: without it the fields are not fields
class InvoicePosted(DomainEvent):
    name = "billing.invoice.v1.posted"   # plain assignment, never `name: str`
    invoice_id: str = ""


@billing.feature(CommandPostInvoice)
class PostInvoice(Feature):
    def execute(self, dto: CommandPostInvoice) -> None:
        invoice = self.repository.get(Invoice, dto.invoice_id)
        invoice.state = "posted"
        invoice.record(InvoicePosted(invoice_id=invoice.id))
        self.repository.save(invoice)
        for event in invoice.pull_events():
            self.publisher.publish(event)


@notifications.feature(InvoicePosted)    # the subscriber is an ordinary Feature on another bus
class EmailTheCustomer(Feature):
    def execute(self, dto: InvoicePosted) -> None: ...
```

## The rules that matter

- **A subscriber is a Feature on a bus**, registered for the event class. N subscribers means N
  buses, not N handlers on one bus (one bus refuses a second Feature for the same DTO).
  `@bus.feature([SomeCommand, SomeEvent])` only when they share one algorithm.
- **An event has two identities; only the wire `name` travels.** Past tense, versioned in the name
  (`v1`); a breaking shape is a new class `v2`. A context that may not import the publisher's
  module declares its own class under the same `name`; prefer sharing one class.
- **The typed publish** (`publish(event, Response)`) holds only on a `SyncQueue` with exactly one
  bus for that event; anything else is refused. What a bus raises, `publish` raises.
- **The outbox is what must be right.** The event row commits in the same transaction as the state
  change; a separate relay publishes. Before commit announces what may not happen; after commit
  loses it on a crash.
- **Event log ≠ event sourcing.** Source only an aggregate whose history is the product.

## References

- [references/recording-and-publishing.md](references/recording-and-publishing.md) — record/pull, Publisher, Sync/Background queues, wiring, tests
- [references/change-tracking.md](references/change-tracking.md) — `ChangeTrackingMixin`, `EntityUpdated`, labels, `caused_by`
- [references/event-sourcing.md](references/event-sourcing.md) — facts as the state, `event_columns()`, append-only
- [references/outbox.md](references/outbox.md) — `EventTrackableMixin`, the outbox table, the relay, dead letters
- [references/brokers.md](references/brokers.md) — FastStream: channels, keys, delivery guarantees

Deep docs in the framework repo (not shipped with the package): `docs/events/README.md`,
`docs/events/change-tracking.md`, `docs/events/brokers.md`, `docs/shapes.md`.

## Related

- Aggregates, `save`, units of work, hooks: `sincpro-framework-persistence`
- Context propagation across buses and threads: `sincpro-framework-core`
- Consuming Commands from a broker (`QueueGateway`, `@queue`): `sincpro-framework-entrypoints`
- Invalidating cached answers from events (`invalidated_by`): `sincpro-framework-caching`
