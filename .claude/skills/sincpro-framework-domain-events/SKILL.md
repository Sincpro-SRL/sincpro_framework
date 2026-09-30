---
name: sincpro-framework-domain-events
description: Record, publish and store domain events with sincpro_framework — DomainEvent, Entity.record/pull_events, Publisher, SyncQueue/BackgroundQueue, change tracking, event sourcing and the transactional outbox, plus Kafka/RabbitMQ/Redis/NATS through FastStream. Use whenever the task says "when X happens, notify/emit/react", publishes an event, subscribes one context to another, builds an audit trail, an outbox/relay, an event store, or wires a broker in a Sincpro Python service.
---

# sincpro-framework-domain-events

Nothing is wired by default and nothing is stored. A project builds the queue it wants with the
buses it names, and publishes when a Feature decides to. What an aggregate recorded lives in memory
and dies with the object.

Full depth: `docs/events/README.md`, `change-tracking.md`, `brokers.md`, `docs/shapes.md`.

## The rule that shapes everything

**The aggregate records; the Feature that saved it publishes.** The aggregate never publishes
itself — a rollback would then undo a fact the world already heard.

```python
class DatasetRegistered(DomainEvent):
    dataset_id: str

dataset.record(DatasetRegistered(dataset_id=dataset.id))     # the aggregate says it; in memory
self.repository.save(dataset)
for event in dataset.pull_events():                          # the Feature pulls, explicitly
    self.publisher.publish(event)

@planning.feature([CommandBuildPlan, DatasetRegistered])     # the subscriber is a Feature
class BuildPlan(Feature): ...

publisher = Publisher(SyncQueue(Subscriber(planning, assurance)))
```

## The pieces

| Piece | Module | What it is |
|---|---|---|
| `DomainEvent` | `ddd/events.py` | An `Entity` with the envelope: `id` (UUID v7), `created_at`, `entity_type`, `entity_id`, `correlation_id`, `causation_id`, `sequence`, `label` |
| `Entity.record` / `pull_events` | `ddd/entity/entity.py` | Records with the aggregate's type, id and sequence; pulling hands them back and forgets them |
| `ChangeTrackingMixin` | `ddd/entity/mixins/tracking.py` | One `EntityUpdated` event per save, with every field that changed |
| `Publisher` | `events/publisher.py` | Emits to a queue, with the signature of a bus |
| `Subscriber` | `events/subscriber.py` | The bus instances handed in; executes the ones whose registry knows the event |
| `SyncQueue` / `BackgroundQueue` | `events/queue.py` | Same process / another process |
| `FastStreamQueue` | `events/faststream/` | Kafka, RabbitMQ, Redis, NATS — `[faststream]` |

## The rules that matter

- **A subscriber is an ordinary Feature on a bus**, registered for the event class:
  `@bus.feature([SomeCommand, SomeEvent])`. N subscribers means N buses, not N handlers on one bus.
- **An event has two identities; only the wire `name` travels.** Name it
  `<context>.<aggregate>.<past-tense verb>` (`bank.v1.money_deposited`). Past tense, because an event
  is a fact that already happened. Version the shape in the name (`v1`); a v2 is a new class.
- **Which wiring you need depends on the system shape** (`docs/shapes.md`): one database → the
  in-process queue is enough; a database per context → an outbox and a relay; the facts are the
  state → event sourcing.
- **The outbox is what must be right.** The event is written **in the same transaction as the state
  change**; a separate relay reads and publishes. Publishing before commit announces what may not
  have happened; publishing after commit loses the event if the process dies in between. There is no
  third correct option.
- **Event log ≠ event sourcing.** The log keeps state *plus* a record of what happened (the default).
  Event sourcing makes the log *be* the state and costs projections, snapshots and event versioning.
  Source only an aggregate whose history is the product.

## References

- [references/recording-and-publishing.md](references/recording-and-publishing.md) — record/pull, Publisher, Sync/Background queues, subscribers
- [references/change-tracking.md](references/change-tracking.md) — `ChangeTrackingMixin`, `EntityUpdated`, labels, `caused_by`
- [references/event-sourcing.md](references/event-sourcing.md) — facts as the state, `event_columns()`, the event store, append-only
- [references/outbox.md](references/outbox.md) — `EventTrackableMixin` + `delivery_columns()`, the relay, dead letters
- [references/brokers.md](references/brokers.md) — FastStream: channels, keys, delivery guarantees, at-least-once

## Related

- Persistence and `save`: `sincpro-framework-persistence`
- Async fan-out and thread context handoff: `docs/core/context-manager.md`
- Broker entrypoint (consuming Commands, not events): `docs/entrypoints/queue.md`
