# PRD_19: Events as entities — one table per bounded context, delivered by a relay, sourced when asked

- **Status**: built — the event table and its mapping, events kept with `save(aggregate)`,
  `DeliverableEventMixin` with per-row delivery state, `EventRelay` and its failure policies,
  `EventSourcedMixin` with `entity_version`, on Postgres, SQLite and the in-memory repository;
  `Crons.run_relay` / `relay_deliverable_events`. Sagas are the next iteration; snapshots of an
  event-sourced entity come after.
- **Depends on**: PRD_17 (the unit of work), the unit of work in play (decision 29),
  `DomainEvent` (`ddd/events/`), the event-driven module (`Publisher`, `Queue`, `Subscriber`),
  the queue entrypoint's inbox (`entrypoints/faststream/inbox.py`).
- **Replaces**: `EventLogEntry`, `EventTrackableMixin`, `Outbox`, `OutboxRelay`, and the first
  shape of this PRD — `EventStore`, `DatabaseEventStore`, `StoredEvent`, `EventStoreQueue`,
  `EventSubscription` with its checkpoint, lease and subscription tables. The ORM is in preview:
  removing them is a patch, not a breaking change.
- **Philosophy**: an event already says what happened, to what, when and why. It needs no
  wrapper, no store of its own and no second API: it is an entity, kept by the repository the
  context already has.
- **Decision record**: [decision 31](../persistence/decisions.md#31-a-bounded-contexts-events-are-entities-in-one-table).

## 1. Problem

Three questions a service meets the moment it emits an event:

1. **Will the event exist if, and only if, the change committed?** Publishing before the commit
   announces what may be rolled back; publishing after it loses the event when the process dies
   or the broker is down between the commit and the send.
2. **Who sends it, and who sends it again?** Something reads what was committed and hands it to a
   broker — once per event, from one replica at a time, again after a failure, in order per
   entity.
3. **What is the history?** An auditor asks what happened to invoice F-123; an account whose
   state *is* its movements is rebuilt from them.

Before this PRD each question had a different piece: an event log saved by hand, a delivery
status mixed onto some events, an outbox table per event type, and — in the first shape of this
PRD — an `EventStore` with its own API and a `StoredEvent` beside the `DomainEvent`, read by
subscriptions with checkpoints. Two entities for one fact, two APIs for one table.

## 2. Background

| System | Where events live | How they leave | Delivery state |
|---|---|---|---|
| **Richardson's transactional outbox** | the outbox table, written in the transaction | a polling publisher, or log tailing | per row, or a position |
| **Microsoft eShop** (`IntegrationEventLogEntry`) | one table per service, the event as JSON | a service publishes what is pending | per row: state, times sent |
| **Wolverine / NServiceBus outbox** | the envelope storage | the durability agent | per message |
| **Debezium outbox router** | an outbox table | change data capture | the connector's offset |
| **KurrentDB, Marten, Axon, Kafka** | an append-only log | subscriptions, consumer groups | the reader's checkpoint |

The checkpoint model is for a log read by many independent readers. Here the context delivers
to one place — a broker — and fan-out, replay and per-consumer retention are what the broker
already does. So the framework takes the outbox model: delivery state per row, read with
`FOR UPDATE SKIP LOCKED`, and leaves the log to Kafka.

## 3. The model

```
bounded context                                         beyond it
───────────────                                         ─────────
Feature
  └─ repository.save(invoice)  ──►  invoice row  ┐ one commit
                                    events rows  ┘
EventRelay.run_once()  ── due deliverable events ──►  Publisher(queue)  ──►  broker  ──►  consumers
       └─ marks each one: delivered · retried · parked · skipped        (inbox: idempotent)
```

1. **One entity.** `DomainEvent` is an `Entity` — UUID v7 id, `created_at` — with its envelope:
   `name`, `entity_type`, `entity_id`, `entity_version`, `correlation_id`, `causation_id`,
   `label`. A subclass declares what it adds.
2. **One table per bounded context.** The context's base class (`BillingDomainEvent`) is mapped
   with `map_events` to a table built by the `event_table` template; every subclass goes to the
   same table, its wire `name` the discriminator, what it adds in a JSON `payload`.
3. **The repository keeps them.** `save(events)`, `search(InvoicePaid, criteria)`,
   `search(BillingDomainEvent, criteria)`; `save(aggregate)` keeps what it recorded in the same
   transaction and leaves them on the aggregate for `pull_events()`.
4. **Deliverable is a mark and a state.** `DeliverableEventMixin` adds `delivered_at`,
   `next_delivery_at` (columns) and `delivery` (JSON for people); the table adds
   `is_deliverable`, set from the class on insert. None of them is ever sent.
5. **The relay is the strategy that delivers.** Over any repository; what a failure does is a
   `DeliveryFailurePolicy`.
6. **Event sourcing when the history is the state.** `EventSourcedMixin`, `entity_version`, and a
   unique constraint that makes a concurrent append a `StaleAggregate`.

## 4. The vocabulary

### 4.1 The context's event table

```python
@dataclass(kw_only=True)
class BillingDomainEvent(DomainEvent):
    name = "billing.v1.event"


@dataclass(kw_only=True)
class InvoicePaid(BillingDomainEvent):
    name = "billing.invoice.v1.paid"
    amount: int = 0


billing_events = event_table("billing_events", metadata)
map_events(mapper_registry, BillingDomainEvent, billing_events)
```

| Column | |
|---|---|
| `id` | primary key, UUID v7: ordering by it is ordering by time |
| `name` | the wire name, the discriminator |
| `entity_type`, `entity_id`, `entity_version` | what it is about; empty for an event about no entity |
| `correlation_id`, `causation_id`, `label`, `created_at` | the envelope |
| `payload` | what the subclass declares (JSONB on Postgres) |
| `is_deliverable`, `delivered_at`, `next_delivery_at`, `delivery` | delivery (§4.3) |

Indexes: `(name, id)`, `(entity_type, entity_id, id)`, a partial index on `next_delivery_at`
where `is_deliverable` and not delivered, and `UNIQUE (entity_type, entity_id, entity_version)`.
A subclass declared after `map_events` is mapped before its first instance is built; a project
maps its base once and never lists its events.

### 4.2 Kept with the change

`save(aggregate)` writes the aggregate and the events it recorded whose class is mapped, in one
flush; a save repeated keeps an event once; a rollback keeps neither. An event no aggregate
records is `repository.save([DayClosed()])`, or `Publisher(RepositoryQueue(repository)).publish`
for code that already publishes. The in-memory repository keeps them the same way.

### 4.3 `DeliverableEventMixin`

| Field | Kept as | Meaning |
|---|---|---|
| `delivered_at` | column | when it went out; empty while not |
| `next_delivery_at` | column | when the relay may try it: now on creation, later after a failure, empty once parked |
| `delivery` | JSON | `{"attempts", "last_error", "parked", "skipped"}`, for people |

Methods `delivered`, `failed`, `parked`, `replayed`; `as_json()` drops the three fields. The
marked class is a contract: declaring it without an explicit versioned name (`….v1.…`) warns.

### 4.4 `EventRelay`

```python
relay = EventRelay(repository, BillingDomainEvent, Publisher(FastStreamQueue(kafka)),
                   on_failure=RetryLater(attempts=5), batch=100)
relay.run_once()     # → RelayPass(read, delivered, retried, held, parked, skipped)
```

1. Inside `repository.context()` when the repository transacts, searches every deliverable
   subclass of `source` for `delivered_at IS NULL AND next_delivery_at <= now`, ordered by id,
   at most `batch` — `FOR UPDATE SKIP LOCKED` where the store has row locks.
2. Hands each one to the publisher, in order. An entity held by a retry later skips its later
   events in the pass; a retry in place ends the pass.
3. Saves every event it touched in the same transaction.

It holds no thread and no timer: a cron (`Crons.run_relay`, `relay_deliverable_events`), a
worker loop, a Feature or a test drives it.

### 4.5 `DeliveryFailurePolicy`

| Policy | An event that fails |
|---|---|
| `RetryInPlace(attempts=5, backoff, then=ParkAndContinue(), never_retry=())` — default | tried again next pass; nothing after it goes first |
| `RetryLater(attempts, backoff, then, holds_stream=True)` | tried at its time; the rest go on; its entity's later events wait |
| `ParkAndContinue()` | kept aside with its reason; `replayed()` + save puts it back |
| `SkipAndContinue()` | marked delivered, the reason in `delivery["skipped"]` |

`ExponentialBackoff` (default) and `FixedBackoff` space attempts. A policy only decides
(`FailureDecision`); the relay acts.

### 4.6 `EventSourcedMixin`

```python
@dataclass
class CustomerAccount(EventSourcedMixin, Entity):
    event_base = BillingDomainEvent
    balance: int = 0

    def deposit(self, amount: int) -> None:
        self.happened(MoneyDeposited(amount=amount))

    def apply(self, event: DomainEvent) -> None:
        if isinstance(event, MoneyDeposited):
            self.balance += event.amount
```

`happened` numbers the event (`entity_version = version + 1`), records it and applies it.
`repository.get` reads the entity's events from `event_base` and rebuilds it (`None` when it
has none); `save` appends the unsaved ones, and no row is written for the entity. Two writers
rebuilt at the same version collide on the unique constraint: `StaleAggregate`.

## 5. Failure scenarios

| What happens | Result |
|---|---|
| the transaction rolls back | neither the change nor its events exist |
| the process dies right after the commit | the events are in the table; the next pass delivers them |
| the broker is down | the policy retries with backoff; after the last attempt the event is parked, the rest go on |
| two replicas run the same relay | `SKIP LOCKED`: each takes different rows; none is delivered twice (tested with four threads on Postgres) |
| a relay dies after sending and before its commit | the events are sent again; the consumer's inbox drops them |
| one invoice's event fails | `RetryLater` holds that invoice's later events; other invoices go on |
| an event that can never succeed | `never_retry` parks it at once |
| two writers of one event-sourced entity | the second gets `StaleAggregate` and rebuilds |
| a new service needs the history | it reads it from the broker (Kafka retention), not from this table |

## 6. Decisions taken, and why

The owner's reasoning, kept in [decision 31](../persistence/decisions.md#31-a-bounded-contexts-events-are-entities-in-one-table):

1. **One entity, `DomainEvent`.** A stored-event wrapper repeated the envelope under other names.
2. **One table per bounded context, single-table inheritance.** One query answers every event,
   one class, or one entity's history.
3. **No event store.** It was a repository by another name; the repository is already agnostic,
   transactional and locking.
4. **Delivery state on the row, no checkpoint and no subscription tables.** The broker fans out
   and replays; per-row state needs no lease and no Postgres visibility rule.
5. **`DeliverableEventMixin`, not `IntegrationEventMixin`.** It marks that an event is
   delivered, wherever to; `is_deliverable` makes it a filter.
6. **Event sourcing is in, with `entity_version`.** `version` is the entity's optimistic lock.
7. **Templates in `orm/sqlalchemy/entrypoint/template_table/`, one file each**; `data_mapper` maps.
8. **Facilitate, never force.** A context with no event table records and pulls events as
   before; `pull_events()` is never taken away.

## 7. Next

1. **Sagas** — a process that listens to events and issues commands, with its own state — the
   next iteration, on top of this table and the relay.
2. **Snapshots** of an event-sourced entity, so a rebuild starts from the last one.
3. A wake-up after commit for lower latency than the cron's interval.
4. Retention, and crypto-shredding for erasing a person from kept events.
