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
from sincpro_framework.orm import event_table, map_events


@dataclass(kw_only=True)
class BillingEvent(DomainEvent):
    pass


@dataclass(kw_only=True)
class InvoiceSent(DeliverableEventMixin, BillingEvent):
    name = "billing.invoice.v1.sent"
    invoice_id: str = ""


billing = registry()
events = event_table("billing_events", billing.metadata)
map_events(billing, BillingEvent, events)
```

Use migrations for schema creation. Delivery columns are supplied by `event_table`:
`is_deliverable`, `delivered_at`, `next_delivery_at`, `delivery`. The mixin excludes delivery
metadata from `as_json()` so retries do not change the published fact's shape.

## The write and the fact commit together

```python
with repository.context() as unit:
    invoice = unit.get_by(Invoice, number="F-003")
    invoice.state = "sent"
    invoice.record(InvoiceSent(invoice_id=invoice.id))
    unit.save(invoice)
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

A pass selects deliverable subclasses whose `delivered_at` is empty and `next_delivery_at`
is due, orders by event id, publishes, and saves the outcomes. On a transactional repository
this is **one transaction**, including publication, not three claim/send/ack transactions.
`FOR UPDATE SKIP LOCKED` is used where the repository advertises row locks. Memory and SQLite
do not prove multi-replica exclusion; use PostgreSQL integration tests for that requirement.

The relay has no timer. Register it with `Crons.run_relay(relay)` or build it through
`Crons.relay_deliverable_events(repository=repository, source=BillingEvent, to=publisher)`.

## Failure and dead letters

- `RetryInPlace(attempts=5)` is the default: schedules a retry and stops the current pass.
- `RetryLater` schedules a retry, holding later events of that entity in the current pass
    when `holds_stream=True`; unrelated entities continue.
- `ParkAndContinue` leaves `next_delivery_at=None` and the reason in `delivery`.
    `event.replayed()` followed by `repository.save(event)` makes it due again.
- `SkipAndContinue` marks it delivered and records the reason. Use only for explicitly
    dispensable events.
- `FixedBackoff` and `ExponentialBackoff` space attempts; `never_retry` avoids futile retries.
- Delivery is at least once: a crash after publication but before commit causes a resend.
    Consumers need idempotency. A nondurable target queue does not become durable just because
    the relay successfully enqueued into it.

**Current ordering limitation:** the held-entity set exists only within one pass. A retry not
yet due is absent from the next selection, so later events can overtake it. Row locks also
do not serialize an entity's entire stream across replicas. Do not promise strict stream
ordering until both scenarios have been addressed and verified on the deployment backend.

## What a relay needs (and what already answers it)

| Need | Answer |
|---|---|
| storage | context base event mapped once with `event_table` / `map_events` |
| delivery state | `DeliverableEventMixin`: `delivered_at`, `next_delivery_at`, `delivery` |
| one pass | `EventRelay.run_once()` returning `RelayPass` counts |
| scheduling | `Crons.run_relay` / `Crons.relay_deliverable_events`, or `Poll(every, relay.run_once)` inside a `Process` (`docs/process/README.md`). The relay has no timer |
| failure behavior | a `DeliveryFailurePolicy`, not a second relay implementation |

Framework evidence: `tests/event_driven/test_relay.py`, `tests/orm/test_events_table.py`,
`docs/persistence/guide.md` section 12.
