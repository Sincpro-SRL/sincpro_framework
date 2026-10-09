---
name: sincpro-framework-domain-events
description: Record, store and deliver domain events with sincpro_framework — DomainEvent, event_table/map_events, EventSourcedMixin, DeliverableEventMixin, EventRelay, RepositoryQueue, Publisher, change tracking and FastStream brokers. Use whenever a task publishes or subscribes to events, builds an audit trail, replays an aggregate, implements an outbox or retry policy, or wires a broker in a Sincpro Python service.
---

# sincpro-framework-domain-events

A domain event is a fact one bounded context states after its state changed, so that other
contexts can react without being called. Nothing is wired by default. With a context event table,
`save(aggregate)` stores its mapped recorded events in the same transaction, without draining
them. Without that mapping they remain in memory. A Feature can publish after commit; durable
delivery uses `DeliverableEventMixin` and `EventRelay` over that same event table.

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
| `DomainEvent` | A past-tense fact: an `Entity` with `id` (UUID v7), timestamps, `entity_type`, `entity_id`, `entity_version`, correlation/causation ids, `label`, and a wire `name` | dataclass | `sincpro_framework.ddd` |
| `name` | The wire identity of an event class (`"billing.invoice.v1.posted"`), a plain class attribute; defaults to the class name. Routing, storage and brokers match on it | setting | (class attribute) |
| `Entity.record` / `pull_events` / `recorded_events` | Store a copy stamped with aggregate type/id / drain the in-memory events / inspect without draining | method | `sincpro_framework.ddd` (on `Entity`) |
| `DomainEvent.caused_by(cause)` | A copy with `causation_id`/`correlation_id` threaded from the incoming event | function | (method) |
| `ChangeTrackingMixin` | Aggregate mixin: one `EntityUpdated` recorded per update, with every field that changed | dataclass mixin | `sincpro_framework.ddd` |
| `EntityUpdated` | The event `ChangeTrackingMixin` records (`changes`, `field_labels`); subclass it for your own name | dataclass (`DomainEvent`) | `sincpro_framework.ddd` |
| `DeliverableEventMixin` | Marks a fact for relay delivery; `delivered_at`, `next_delivery_at`, `delivery` are stored but excluded from its wire JSON | dataclass mixin | `sincpro_framework.ddd` |
| `EventSourcedMixin` | No aggregate row: `happened` records/applies/numbers a fact; `get` rebuilds and `save` appends | mixin | `sincpro_framework.ddd` |
| `Publisher` / `AsyncPublisher` | What a Feature holds: `publish(event)` or `publish(event, Response)`, into a `Queue` | function | `sincpro_framework.event_driven` |
| `Queue` | Protocol: `put(event)` / `aput(event)` | port (abstract) | `sincpro_framework.event_driven` |
| `SyncQueue` | Runs the subscriber inside the `publish` call, same process | adapter | `sincpro_framework.event_driven` |
| `BackgroundQueue` | A spawned child consumes from a `multiprocessing.Queue`; `start()`/`stop()`. That child is this queue's, not `sincpro_framework.process.Process` | adapter | `sincpro_framework.event_driven` |
| `Subscriber` / `AsyncSubscriber` | The buses that hear events; executes every bus whose registry knows `event.name` | registry | `sincpro_framework.event_driven` |
| `@bus.feature(SomeEvent)` | The subscription itself: an ordinary Feature registered for the event class | decorator | (`UseFramework`) |
| `RecordingQueue` | Test queue that keeps what was published (`.of(Event)`), optionally forwarding | adapter | `sincpro_framework.testing` |
| `FastStreamQueue` | A `Queue` that sends to Kafka/RabbitMQ/Redis/NATS — extra `[faststream]` | adapter | `sincpro_framework.event_driven.adapters.faststream` |
| `subscribe(broker, subscriber)` | Consumer side: one subscription per channel of the events the buses registered, acknowledged manually | function | `sincpro_framework.entrypoints.faststream` |
| `keyed_by_entity` / `by_event_name` | Kafka message key = `entity_id` / channel = event name | function | `sincpro_framework.event_driven.adapters.faststream` |
| `QueueOptions` | Consumer settlement: `inbox`, `max_attempts`, dead-letter suffix, `subscription_of` | setting | `sincpro_framework.entrypoints.faststream` |
| `event_table(name, metadata)` / `map_events(registry, base, table)` | One event table per context; subclasses selected by wire name, extra fields in JSON payload | function | `sincpro_framework.orm` |
| `event_columns()` | Low-level envelope columns; prefer the complete `event_table` for context event storage | function | `sincpro_framework.orm` |
| `RepositoryQueue` | Stores a published event through the repository for later relay delivery | adapter | `sincpro_framework.event_driven` |
| `EventRelay` / `RelayPass` | `run_once()` selects due deliverable events, publishes and saves outcomes; returns counts | entrypoint / DTO | `sincpro_framework.event_driven` |
| `DeliveryFailurePolicy` | `RetryInPlace`, `RetryLater`, `ParkAndContinue`, `SkipAndContinue`; fixed/exponential backoff | strategy | `sincpro_framework.event_driven` |

Look-alikes: `Queue` (carries events) ≠ `QueueGateway` (consumes Commands). `ChangeTrackingMixin`
(on an aggregate, *records* `EntityUpdated`) ≠ `DeliverableEventMixin` (on an event, *tracks its
delivery*). Event log (state + history) ≠ event sourcing (history *is* the state).

## Architecture

**Inside the framework.** `sincpro_framework/ddd/` holds the vocabulary (`DomainEvent`,
`DeliverableEventMixin`, `EventSourcedMixin`, `Entity.record`, `ChangeTrackingMixin`). `sincpro_framework/event_driven/` holds
`Publisher`, `Subscriber`, the `Queue` protocol and its two standard-library adapters
(`SyncQueue`, `BackgroundQueue`). Optional extras, never core dependencies:
`event_driven/adapters/faststream/` sends; `entrypoints/faststream/` subscribes (`[faststream]`
plus the broker driver, e.g. `faststream[kafka]`), the event
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
    outbox_relay.py                  # drives EventRelay; or register it through Crons
```

`common/` imports no sibling: each context adds itself to `BUSES`, and the `SyncQueue` function
asks for the buses only on the first publish, when all of them exist.

**The flow of one fact:**

```
Feature.execute(command)
  aggregate.record(Event)        in memory on the aggregate (a stamped copy)
  repository.save(aggregate)     state + mapped events; ChangeTrackingMixin records EntityUpdated
  aggregate.pull_events()        handed over once, then forgotten
  publisher.publish(event) ──► Queue.put(event)
      SyncQueue        → Subscriber.handle → each bus with event.name registered → its Feature, in this call
      BackgroundQueue  → multiprocessing.Queue → worker: build_subscriber().handle(event)
      FastStreamQueue  → broker channel (event.name) → subscribe(...) in the consumer → its buses
outbox: one unit of work saves state + mapped facts → EventRelay reads due events → publish → delivered
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
- **Copying the old event-entry/status model.** Use the context base event, `event_table` and
  `map_events`; delivery is metadata on that event. There is no separate outbox row to maintain.
- **Direct publication plus a relay for the same fact.** Saving keeps the event without draining
  it; choose one delivery owner or subscribers receive both sends.
- **Assuming strict stream ordering from the relay.** Holding later events currently lasts only
  for the current pass, not across backoff intervals or replicas; see `references/outbox.md`.
- **Channel mismatch.** A producer using `channel_of=` and a consumer calling `subscribe` without
  the matching `channel_of_name=` never meet.
- **Two services on one event over RabbitMQ.** Both consume the queue named after the channel and
  *compete*: each sees part of the events. Use `QueueOptions(subscription_of=...)` with an exchange
  per group, or Kafka/NATS consumer groups.
- **Trusting `BackgroundQueue`, Redis Pub/Sub or core NATS with a fact that matters.** All are at
  most once; a `BackgroundQueue` failure is logged in its child process only. Driving an outbox
  is a cron or a `Poll` (`sincpro-framework-operations`), not this child.

## The rule that shapes everything

**The aggregate records; storage and delivery are separate.** The Feature saves, the repository
keeps mapped events, and a relay delivers durable facts. Manual publication is an explicit
post-commit alternative. The aggregate never publishes: a rollback could undo that fact.

```python
from dataclasses import dataclass

from sincpro_framework.ddd import DomainEvent, Entity
from sincpro_framework.event_driven import Publisher, Subscriber, SyncQueue


@dataclass(kw_only=True)                 # required: without it the fields are not fields
class InvoicePosted(DomainEvent):
    name = "billing.invoice.v1.posted"   # plain assignment, never `name: str`
    invoice_id: str = ""


# domain/invoice.py — the rule and the fact live on the aggregate
@dataclass
class Invoice(Entity):
    state: str = "draft"

    def post(self) -> None:
        self.state = "posted"
        self.record(InvoicePosted(invoice_id=self.id))


@billing.feature(CommandPostInvoice)
class PostInvoice(Feature):
    def execute(self, dto: CommandPostInvoice) -> None:
        invoice = self.repository.get(Invoice, dto.invoice_id)
        invoice.post()
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
- **Event storage ≠ event sourcing.** For an audit trail map a context base `DomainEvent`
  with `event_table` / `map_events`. Save the row-backed aggregate normally; query the base
  for the context's history or a subclass for one event type. Use `EventSourcedMixin` only
  when the facts themselves are the aggregate's state. Never add a parallel `...EventEntry`.

## The published language: what other processes depend on

A context's events are defined in its `domain/` — the aggregate records them — but **what the
outside may depend on is chosen in `entrypoints/events.py`**: an explicit, versioned contract.
n8n, another service or whoever listens to a webhook imports that module, never `domain/`.

```text
domains/issue/
  domain/events.py          every fact the aggregates record (public and internal)
  entrypoints/events.py     PUBLISHED = (IssueOpened, IssueClosed, …); explicit __all__; catalog()
```

```python
# entrypoints/events.py — the contract; nothing here is defined, only chosen
from my_service.domains.issue.domain.events import IssueClosed, IssueOpened

PUBLISHED = (IssueOpened, IssueClosed)
__all__ = ["IssueOpened", "IssueClosed", "PUBLISHED"]   # explicit: readable as a contract
```

- **The name is the contract.** `<context>.<aggregate>.v1.<fact>` is what travels. A breaking
  change is a **new class** (`IssueClosedV2`, `name = "….v2.closed"`), never an edit of a v1 one;
  both are exported while consumers migrate, and v1 leaves when nobody reads it.
- **The direction never flips.** `entrypoints/` imports `domain/`, never the reverse — so the
  events cannot *move* to `entrypoints/`, only be published from there.
- **Only an entrypoint imports an entrypoint.** Inside the same service, another context that
  reacts declares its own class under the same `name` (two identities, one travels).
- **One `domain/events.py` while there is only v1.** Split it into `domain/events/{v1,v2}.py` the
  day a v2 appears; `entrypoints/events.py` keeps the same shape, so no consumer notices.
- **The aggregate records the current version only.** If a consumer still needs v1 after the
  domain moved to v2, translate in `entrypoints/`, not in the domain.
- **Stored history still needs its classes.** Import compatible historical event classes while
  their rows remain: the polymorphic ORM resolves `name` to a mapped class. JSON alone does
  not supply an unknown discriminator. Removing a historical class needs a migration plan.

## References

- [references/recording-and-publishing.md](references/recording-and-publishing.md) — record/pull, Publisher, Sync/Background queues, wiring, tests
- [references/change-tracking.md](references/change-tracking.md) — `ChangeTrackingMixin`, `EntityUpdated`, labels, `caused_by`
- [references/event-sourcing.md](references/event-sourcing.md) — `EventSourcedMixin`, `happened`/`apply`, replay, concurrent writers
- [references/outbox.md](references/outbox.md) — `DeliverableEventMixin`, event table, `EventRelay`, retry policies
- [references/brokers.md](references/brokers.md) — FastStream: channels, keys, delivery guarantees

Deep docs in the framework repo (not shipped with the package): `docs/events/README.md`,
`docs/events/change-tracking.md`, `docs/events/brokers.md`, `docs/shapes.md`.

## Related

- Aggregates, `save`, units of work, hooks: `sincpro-framework-persistence`
- Context propagation across buses and threads: `sincpro-framework-core`
- Consuming Commands from a broker (`QueueGateway`, `@queue`): `sincpro-framework-entrypoints`
- Invalidating cached answers from events (`invalidated_by`): `sincpro-framework-caching`
