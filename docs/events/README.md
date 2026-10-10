# Events: entities kept with the change, delivered by a relay, carried by queues to buses

For an existing service, read [upgrading.md](upgrading.md) before changing imports or migrating
history. It records the main revision, removed APIs and verified delivery/query limitations.

```python
@dataclass(kw_only=True)
class DatasetRegistered(CatalogDomainEvent):                 # the context's base event class
    name = "catalog.dataset.v1.registered"
    dataset_id: str

dataset.record(DatasetRegistered(dataset_id=dataset.id))     # the aggregate says it; in memory
self.repository.save(dataset)                                # kept with it, when the context has an event table
for event in dataset.pull_events():                          # the Feature may still pull and publish
    self.publisher.publish(event)

@planning.feature([CommandBuildPlan, DatasetRegistered])     # the subscriber is a Feature
class BuildPlan(Feature): ...

publisher = Publisher(SyncQueue(Subscriber(planning, assurance)))
```

Nothing is wired by default. A project builds the queue it wants with the buses it names, and
publishes when a Feature decides to. What an aggregate recorded lives in memory and dies with the
object — unless the context gives its events a table. **A domain event is an entity**: the
context maps its base event class to one table (`event_table`, `map_events`), every subclass
goes there, and the repository it already has keeps and queries them — `save(aggregate)` in the
same commit as the change, `search(InvoicePaid, criteria)` like any entity. What must leave the
context is marked `DeliverableEventMixin` and sent on by an `EventRelay`:

```
   save(aggregate) · save(events)        the events, in the same commit as the change
            │
   the context's event table             one table · one row per event · its class by name
            │  deliverable, not delivered, due
   EventRelay                            SKIP LOCKED across replicas · a failure policy · marks each row
            │
   Publisher(Queue)                      SyncQueue · BackgroundQueue · FastStreamQueue
            │
   Subscriber(buses) · the broker's consumers
```

That is the transactional outbox with the event table as the outbox. An entity whose state *is*
its events is `EventSourcedMixin`, rebuilt from the same table. How to use it is
[persistence guide §10–§12](../persistence/guide.md#10-change-tracking-and-the-contexts-event-table);
why it is shaped this way is [PRD_19](../prd/PRD_19_events-as-entities.md) and
[decision 31](../persistence/decisions.md#31-a-bounded-contexts-events-are-entities-in-one-table).

**Which of these you need depends on the shape of your system** — one database, a database
per context, or the events *as* the state. [shapes.md](../shapes.md) picks the wiring; this page
is the parts.

## The pieces

| Piece | Module | What it is |
|---|---|---|
| `DomainEvent` | `ddd/events/domain_event.py` | An `Entity` with the envelope: `id` (UUID v7), `created_at`, `entity_type`, `entity_id`, `entity_version`, `correlation_id`, `causation_id`, `label` |
| `Entity.record` / `pull_events` | `ddd/entity/entity.py` | Records with the aggregate's type and id; pulling hands them back and forgets them |
| `ChangeTrackingMixin` | `ddd/entity/mixins/change_tracking.py` | One `Updated` event with every field that changed — see [change-tracking.md](change-tracking.md) |
| `DeliverableEventMixin` | `ddd/events/mixins/deliverable.py` | Marks an event delivered beyond the context, and keeps its delivery: `delivered_at`, `next_delivery_at`, `delivery` |
| `EventSourcedMixin` | `ddd/entity/mixins/event_sourced.py` | An entity whose state is its events: `happened`, `apply`, rebuilt by `get`, appended by `save` |
| `event_table` / `map_events` | `orm/sqlalchemy/entrypoint/template_table/events.py`, `orm/sqlalchemy/services/event_mapping.py` | The context's one event table, and its base class mapped to it — every subclass with it |
| `EventRelay` | `event_driven/entrypoint/relay.py` | Delivers the deliverable events not delivered and due, over any repository; `run_once()` → `RelayPass` |
| `DeliveryFailurePolicy` | `event_driven/domain/failure.py` | `RetryInPlace` · `RetryLater` · `ParkAndContinue` · `SkipAndContinue`; `ExponentialBackoff`, `FixedBackoff` |
| `Publisher` | `event_driven/entrypoint/publisher.py` | Emits to a queue, with the signature of a bus |
| `Subscriber` | `event_driven/entrypoint/subscriber.py` | The bus instances handed in explicitly; executes the ones whose registry knows the event — every one, whatever another raised |
| `Queue` | `event_driven/domain/queue.py` | The protocol: `put` and `aput` |
| `SyncQueue` | `event_driven/adapters/sync_queue.py` | Same process; `publish` runs the subscriber and returns |
| `BackgroundQueue` | `event_driven/adapters/background_queue.py` | Another process consumes; `start()` / `stop()`; not durable |
| `RepositoryQueue` | `event_driven/adapters/repository_queue.py` | `publish` keeps the event through the repository, in the unit of work in play, for the relay |

**A bus declares nothing.** Its registry already says which DTOs it answers, an event is a DTO,
and the subscriber executes the bus with it. `@bus.feature([SomeCommand, SomeEvent])` is the
whole subscription; `Subscriber(planning, assurance)` is the whole wiring.

## `Publisher`: the signature of a bus

```python
publisher.publish(event)                               →  None           launch and forget
publisher.publish(event, ResponseDTO)                  →  ResponseDTO    wait for the answer
await publisher.get_async_publisher().publish(event, ResponseDTO)
```

The typed form is the same promise `UseFramework.__call__` makes for a command. It holds only
where somebody can answer in the same call: a `SyncQueue` with exactly one bus for that event. A
`BackgroundQueue` cannot answer from another process, and two buses have no one answer, so both
are refused rather than guessed. What a bus raises, the publisher raises.

`Subscriber.handle(event)` and `subscriber.get_async_subscriber().handle(event)` are the same two
forms on the receiving side; the async one runs each bus through its own `get_async_bus()`.

## The two queues

**`SyncQueue(subscriber)`** — `publish` runs the buses where it was called and returns. For
development, tests, and the reactions that must be visible at once.

It takes a **function** too, and that form is what a composition root wants:

```python
BUSES: dict[str, UseFramework] = {}
publisher = Publisher(SyncQueue(lambda: Subscriber(*BUSES.values())))   # before any bus exists
```

A queue needs a subscriber, a subscriber needs the buses, and a bus needs the publisher the
queue is behind — wire that in one breath and it is a circle. Handed a function, the queue asks
for nothing until somebody publishes, and by then every bus is registered. Built once, on that
first publish, and kept. It is the same shape `BackgroundQueue` has always had, for a different
reason: there, the function runs in the worker because a bus cannot cross a process.

**Publishing runs the subscriber in the call**, so a subscriber that publishes re-enters the
queue depth-first — a listener registered after it hears the *inner* fact first. Where the order
of facts matters, write the fact down in the context it happened in, or send it through an
outbox whose relay keeps them in order (a waiting retry holds back what comes after it).

**`BackgroundQueue(build_subscriber)`** — the smallest «do it in the background»:

```python
def build_subscriber() -> Subscriber:      # a module-level function: it runs in the worker
    return Subscriber(planning, assurance)

queue = BackgroundQueue(build_subscriber).start()
Publisher(queue).publish(event)            # into a multiprocessing.Queue, and back at once
queue.stop()
```

`start()` spawns the worker process. The worker calls `build_subscriber()` — a bus does not cross
a process, so it builds its own — and then waits on the queue: one event, one `handle`, wait again.
The event crosses as its **wire name** and JSON, and is rebuilt as the class the subscriber
knows. `stop()` sends the stop signal and waits for the worker. A queue that was never started
refuses a publish rather than accepting the event and dropping it.

## Reading the log

A `DomainEvent` is an entity, so its context's event class is read like any entity:
`EntityReads[ProjectEvent]` answers `Get`, `GetMany` and `Search`, and a record's history is
`DomainEvents[Issue, R]` on the record's own reads ([entity-reads](../persistence/entity-reads.md)).
The envelope is columns, so every question about the log is a criteria:

| Question | Criteria |
|---|---|
| one record's events | `DomainEvents[Issue, R]` (`names=[...]` for some types), or `history_of(issue)` |
| several records together | `history_of(issue, *runs)` — each by its type and identity |
| events of some types | `name in ["project.issue.v1.opened", "project.issue.v1.closed"]` |
| events of one kind of record | `entity_type = "Issue"` |
| events about no record | `entity_type = ""` |
| a time range | `created_at between [start, end]` |
| everything one request caused | `correlation_id = …` |

`name` is the wire name — a class attribute, filtered like a column in every store. What a
class adds lives in `payload` and is not filtered; an event that must be found by its own
fields is mapped to a table of its own (`event_columns()`), or read through a model built for it.

**Application events.** An event about no record is first-class — same table, same reads.
Before storing one with an empty `entity_type`, give it a subject when it has one: a closing
day, an import, an execution — `ExecutionCompleted` and `ExecutionFailed` already name
`entity_type="Execution"` — so its history can be asked for like a record's. An empty subject
stays for what truly has none, read with `entity_type = ""`.

## An event has two identities, and only one of them travels

`name` is what an event is called on the wire; the Python class is what it is here. A context
that may not import the publisher's module declares its own class under the same `name`, and
the subscriber hands each bus **the class that bus registered** — so a fact reaches every
listener whether or not they share a class.

Sharing one class across the buses is simpler and cannot drift, and is what most projects
should do. The rebuild is there for when they genuinely cannot.

## The published language: the events other processes may depend on

The events a context records are defined in its `domain/`; the ones the outside may depend on are
**chosen** in its `entrypoints/events.py`. That module defines nothing: it imports the public facts
from the domain, lists them in `PUBLISHED`, exports them with an explicit `__all__`, and can offer a
`catalog()` (wire name → fields) for whoever integrates. n8n, another service or a webhook consumer
import it, never the domain — so the domain can change its internals without breaking them.

Versioning lives in the **name**, not in the file layout. `issue.v1.closed` and `issue.v2.closed` are
two classes; while only v1 exists, one `domain/events.py` is enough, and the day a v2 appears it can
become `domain/events/{v1,v2}.py` without consumers noticing, because they import from
`entrypoints/events.py`. A breaking change is always a new class; both versions are published while
consumers migrate. The aggregate records the current version only: a consumer that still needs v1
gets a translation in `entrypoints/`, never a second `record` in the domain. A kept event of an old
version is still its own class: the table keeps the wire name and the payload. Historical
classes must remain imported and mapped while those rows exist; an unknown discriminator
cannot be reconstructed just because its JSON is still present. Removing a class requires
an explicit migration or compatibility plan.

The direction never flips: `entrypoints/` imports `domain/`, and only an entrypoint imports another
entrypoint. Inside one service, a context that reacts to another's facts declares its own class
under the same name — the two identities above.

An event recorded, published or saved inside an execution is caused by it and joins its flow
(`causation_id`, `correlation_id`) unless it already says otherwise, and the execution it starts is
caused by the event: one chain of executions and events —
[context-manager.md](../core/context-manager.md#execution-identity).

Kafka, RabbitMQ, Redis and NATS are one more `Queue` through FastStream, behind the
`[faststream]` extra — the same
`publish(event)` and the same buses: [brokers.md](brokers.md).
