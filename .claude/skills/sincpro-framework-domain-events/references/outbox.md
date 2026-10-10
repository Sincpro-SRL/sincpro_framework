# The transactional outbox

Use this when a fact must survive a crash between the state change and delivery. The context's
event table is the outbox: `save(aggregate)` keeps its mapped events in the same transaction.
`DeliverableEventMixin` marks the events to send, and `EventRelay` polls and updates delivery.
Do not introduce a second event-entry model or a hand-written claim loop.

## The table

```python
from dataclasses import dataclass

from sqlalchemy.orm import registry

from sincpro_framework.ddd import DeliverableEventMixin, DomainEvent
from sincpro_framework.data_layer.orm import map_events, template_table


@dataclass(kw_only=True)
class BillingEvent(DomainEvent):
    pass


@dataclass(kw_only=True)
class InvoiceSent(DeliverableEventMixin, BillingEvent):
    name = "billing.invoice.v1.sent"
    invoice_id: str = ""


billing = registry()
events = template_table.event_table("billing_events", billing.metadata)
map_events(billing, BillingEvent, events)
```

Use migrations for schema creation. Delivery columns are supplied by `event_table`:
`is_deliverable`, `delivered_at`, `next_delivery_at`, `delivery`. The mixin excludes delivery
metadata from `as_json()` so retries do not change the published fact's shape.

## The write and the fact commit together

```python
# domain/invoice.py
class Invoice(Entity):
    def send(self) -> None:
        self.state = "sent"
        self.record(InvoiceSent(invoice_id=self.id))     # stamped with entity_type / entity_id


# the Feature
with repository.context() as unit:
    invoice = unit.get_by(Invoice, number="F-003")
    invoice.send()
    unit.save(invoice)                                    # the row and the recorded fact, together
```

Either both land or neither does. Do not also publish these events directly: the relay owns
delivery. For a standalone event use `unit.save(event)` or
`Publisher(RepositoryQueue(repository)).publish(event)` inside the same database unit of work.
`RepositoryQueue` persists; it does not execute subscribers or return their typed responses.

## The relay: claim, deliver, acknowledge

```python
from sincpro_framework.event_driven import EventRelay, Publisher, RetryLater

relay = EventRelay(
    repository,
    source=BillingEvent,
    publisher=Publisher(queue),
    on_failure=RetryLater(attempts=5),
    batch=100,
)
result = relay.run_once()
```

A pass selects, in one query on the context's base class, the deliverable events whose
`delivered_at` is empty and `next_delivery_at` is due, orders by event id, holds what a waiting
retry holds back, publishes, and saves the outcomes. On a transactional repository
this is **one transaction**, including publication, not three claim/send/ack transactions.
`FOR UPDATE SKIP LOCKED` is used where the repository advertises row locks. Memory and SQLite
do not prove multi-replica exclusion; use PostgreSQL integration tests for that requirement.

The relay has no timer. Register it with `Crons.run_relay(relay)` or build it through
`Crons.relay_deliverable_events(repository=repository, source=BillingEvent, to=publisher)`.

## Failure and dead letters

- `RetryInPlace(attempts=5)` is the default: schedules a retry; nothing after it goes out —
    in this pass or the next ones — until it did or was parked.
- `RetryLater` schedules a retry; with `holds_stream=True` (as it comes) the later events of
    that entity wait until it went out or was parked; unrelated entities continue.
- `ParkAndContinue` leaves `next_delivery_at=None` and the reason in `delivery`. A parked event
    holds nothing: its entity's later events go on. `event.replayed()` followed by
    `repository.save(event)` makes it due again.
- `SkipAndContinue` marks it delivered and records the reason. Use only for explicitly
    dispensable events.
- `FixedBackoff` and `ExponentialBackoff` space attempts. The kinds a consumer dead-letters
    (`common.failures.PERMANENT`) and the errors in `never_retry` go to `then` at once; an error's
    `retry_after` is waited before the next attempt.
- Delivery is at least once: a crash after publication but before commit causes a resend.
    Consumers need idempotency. A nondurable target queue does not become durable just because
    the relay successfully enqueued into it.

**Ordering across replicas:** Row locks keep two replicas from taking the same event, not an entity's whole stream: two replicas can each take a different event of one entity in the same instant. Where that matters, run one relay per context, or key the broker by entity (`keyed_by_entity`).

## What a relay needs (and what already answers it)

| Need | Answer |
|---|---|
| storage | context base event mapped once with `event_table` / `map_events` |
| delivery state | `DeliverableEventMixin`: `delivered_at`, `next_delivery_at`, `delivery` |
| one pass | `EventRelay.run_once()` returning `RelayPass` counts |
| scheduling | `Crons.run_relay` / `Crons.relay_deliverable_events`, or `Poll(every, relay.run_once)` inside a `Process` (`docs/process/README.md`). The relay has no timer |
| failure behavior | a `DeliveryFailurePolicy`, not a second relay implementation |

Framework evidence: `tests/event_driven/test_relay.py`, `tests/data_layer/orm/test_events_table.py`,
`docs/persistence/guide.md` section 12.
