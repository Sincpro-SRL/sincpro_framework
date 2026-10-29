# Events: entities stored with the change, published by a Publisher, carried by queues to buses

For an existing service, read [upgrading.md](upgrading.md) before changing imports or migrating
history.

```python
@dataclass(kw_only=True)
class DatasetRegistered(CatalogDomainEvent):                 # the context's base event class
    name = "catalog.dataset.v1.registered"
    dataset_id: str

dataset.record(DatasetRegistered(dataset_id=dataset.id))     # the aggregate says it; in memory
self.repository.save(dataset)                                # stored with it, when the context has an event table
for event in dataset.pull_events():                          # the Feature publishes what it decides
    self.publisher.publish(event)

@planning.feature([CommandBuildPlan, DatasetRegistered])     # the subscriber is a Feature
class BuildPlan(Feature): ...

publisher = Publisher(SyncQueue(Subscriber(planning, assurance)))
```

Nothing is wired by default. A project builds the queue it wants with the buses it names, and
publishes when a Feature decides to — or has its repository publish what a write recorded once
it committed. Two concepts, kept apart:

- **Event storing.** What an aggregate recorded lives in memory and dies with the object —
  unless the context gives its events a table. **A domain event is an entity**: the context maps
  its base event class to one table (`event_table`, `map_events`), every subclass goes there, and
  the repository it already has stores and queries them — `save(aggregate)` in the same commit
  as the change, `search(InvoicePaid, criteria)` like any entity.
- **Event-driven.** A `Publisher` puts a message into a queue, and the queue carries it to whoever
  listens. It sends in the call, or — with `forwarding` — holds the message in a line and sends it
  in order on a thread of its own while the broker is down.

They meet in one place, opted into per repository: `publish_on_commit=` hands the
`DeliverableEventMixin` events a write recorded to a publisher once the write committed, and
stamps each stored row with when it went out.

```
   save(aggregate)                       the events, in the same commit as the change
            │
   the context's event table             one table · one row per event · its class by name
            │
            │  after the commit          publish_on_commit= · or the Feature: publisher.publish(event)
            ▼
   Publisher(Queue)                      sends in the call
            │  forwarding=True           held in a line in memory · sent in order · waits the broker out
            ▼
   Queue                                 SyncQueue · BackgroundQueue · FastStreamQueue · AWS
            │
   Subscriber(buses) · the broker's consumers
```

An entity whose state *is* its events is `EventSourcedMixin`, rebuilt from the same table. How
to use it is
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
| `ChangeTrackingMixin` | `ddd/events/mixins/change_tracking.py` | One `Updated` event with every field that changed — see [change-tracking.md](change-tracking.md) |
| `DeliverableEventMixin` | `ddd/events/mixins/deliverable.py` | Marks a contract other contexts hear — warned without a versioned `name` — and stores when it went out: `delivered_at`, `delivery` |
| `EventSourcedMixin` | `ddd/entity/mixins/event_sourced.py` | An entity whose state is its events: `happened`, `apply`, rebuilt by `get`, appended by `save` |
| `event_table` / `map_events` | `orm/sqlalchemy/entrypoint/template_table/events.py`, `orm/sqlalchemy/services/event_mapping.py` | The context's one event table, and its base class mapped to it — every subclass with it |
| `PublishOnCommit` | `ddd/repositories/publication.py` | What a write recorded, handed to a publisher once it committed, and the stored row stamped — `publish_on_commit=` on `Repository` and `MemoryRepository` |
| `Publisher` | `event_driven/entrypoint/publisher.py` → `services/publication.py` | Puts any message into a queue, with the signature of a bus; `forwarding=` holds and forwards |
| `Forwarding` | `event_driven/services/forwarding.py` | Store-and-forward in the process: a line in memory, one thread, a `CircuitBreaker`, backoff |
| `Forwarded` / `ForwardingHook` | `event_driven/domain/forwarding.py` | How one message went (`attempts`, `delivered_at`, `last_error`, `set_aside`), and the hook told of every delivery and failed attempt |
| `Subscriber` | `event_driven/entrypoint/subscriber.py` → `services/dispatch.py` | The bus instances handed in explicitly; executes the ones whose registry knows the event — every one, whatever another raised |
| `Queue` | `event_driven/domain/queue.py` | The protocol: `put` and `aput` |
| `Envelope` | `event_driven/domain/envelope.py` | What travels with a message on a broker: `Envelope.headers_for(message, source=...)` stamps it, `Envelope.of(headers)` reads it |
| `SyncQueue` | `event_driven/adapters/sync_queue.py` | Same process; `publish` runs the subscriber and returns |
| `BackgroundQueue` | `event_driven/adapters/background_queue.py` | Another process consumes; `start()` / `stop()`; not durable |

**A bus declares nothing.** Its registry already says which DTOs it answers, an event is a DTO,
and the subscriber executes the bus with it. `@bus.feature([SomeCommand, SomeEvent])` is the
whole subscription; `Subscriber(planning, assurance)` is the whole wiring.

**A redelivery runs again only what failed — when told where to remember.** Delivery is at least
once: a broker delivers an event again when a bus failed, and every bus that answers it runs
again, so a handler of an event is idempotent. `Subscriber(planning, assurance, receipts=store)`
stores which bus handled which event (`keep_for`, a day by default) in a `KeyValueStore` —
`RedisKeyValue`, `DynamoKeyValue`, shared by the replicas — and a redelivery then runs only the
bus that failed.

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

**Any message.** The publisher refuses nothing. A `DomainEvent` is the one this framework
recommends: published inside an execution it goes out caused by it and in its flow
(`causation_id`, `correlation_id`), as a copy — the one handed in is untouched. A message of the
project's own goes into the queue as it is, and what the queue can carry is the queue's to say:
`FastStreamQueue` writes a `DomainEvent` or a `DataTransferObject` as its own JSON (`as_json`),
and anything else with `serializer=`, on the channel `channel_of=` names when it has no `name` —
without them it raises a `ProgrammingError` naming the option. The AWS queues write
`DomainEvent`s.

```python
FastStreamQueue(broker, channel_of=lambda message: "tickets", serializer=json.dumps)
```

`Subscriber.handle(event)` and `subscriber.get_async_subscriber().handle(event)` are the same two
forms on the receiving side; the async one runs each bus through its own `get_async_bus()`.

## Forwarding: held, and sent in order

`Publisher(queue)` sends in the call: a broker that is down fails the `publish` where it was
called. With `forwarding`, nobody waits:

```python
from sincpro_framework.circuit_breaker import CircuitBreaker
from sincpro_framework.event_driven import Forwarded, Forwarding, ForwardingHook, Publisher

rabbit = Publisher(queue, forwarding=True)                 # the defaults
future = rabbit.publish(InvoicePaid(invoice_id="F-1"))     # returns at once
future.result()                                            # Forwarded(attempts=1, delivered_at=…)


class Alerted(ForwardingHook):
    def after_failure(self, message, forwarded: Forwarded) -> None:
        if forwarded.set_aside:
            alerts.send(f"{message.name} never goes: {forwarded.last_error}")


rabbit = Publisher(
    queue,
    forwarding=Forwarding(
        name="rabbit",
        breaker=CircuitBreaker("rabbit", failures=3),
        hooks=[Alerted()],
        wait=timedelta(seconds=1),
        longest_wait=timedelta(minutes=1),
    ),
)
```

| What | How |
|---|---|
| the line | `publish` puts the message at the end of a line in memory and returns a `Future[Forwarded]` |
| one thread | one daemon thread per `Forwarding`, started by the first message, sends one message at a time, in order — nothing to deploy beside the service: no cron, no worker process |
| a failed attempt | the message stays first; the thread sleeps the backoff — from `wait`, doubled each attempt, up to `longest_wait` — or the error's `retry_after`, whichever is longer, and tries it again |
| a broker that keeps failing | the attempts go through a `CircuitBreaker` stored where the context stores its circuits: open, it stops trying until the broker may be back; replicas that share a store open and close together |
| a message that never goes | an error that fails the same way every time (`PERMANENT`: invalid, a refusal) sets it aside — `Forwarded(set_aside=True)` — so it never holds the line |
| its context | each message is sent in the context it was published in — its trace, its execution, its tenant |
| its hooks | `ForwardingHook.after_delivery(message, forwarded)` and `after_failure(message, forwarded)`, run on the forwarding's thread; one that raises is logged and the line goes on |

`Forwarded(attempts, delivered_at, last_error, set_aside)` is how one message went. A typed
publish — `publish(event, ResponseDTO)` — through a forwarding raises a `ProgrammingError`: it
returns before the message went out. One `Forwarding` forwards to one queue.

**Gone with the process.** The line lives in this process's memory: a message still in it when
the process dies is not sent. A fact that must survive that is stored with the change that made it
true, in the event table, and its row says whether it went out — the framework ships no outbox
that would send it again.

## Publishing on commit

The opt-in is per repository — where each bounded context already wires its store, so it holds
for the whole context:

```python
from sincpro_framework.data_layer.orm import Repository
from sincpro_framework.ddd.repositories import PublishOnCommit

Repository(database, billing_hooks, publish_on_commit=rabbit)       # every deliverable event
Repository(
    database,
    publish_on_commit=PublishOnCommit(rabbit, entities=[Invoice, Payment], when=lambda event: …),
)


class InvoiceEvents(PublishOnCommit):
    def publishes(self, event) -> bool: ...        # what goes out
    def publisher_of(self, event) -> Publisher: ...  # a publisher per event
```

`MemoryRepository(publish_on_commit=...)` takes the same. By default what goes out is every
`DeliverableEventMixin` event the write recorded; `entities` narrows it to what those aggregates
recorded (by the recording aggregate's class name) and `when` to what it admits, both on top of
the mark. A subclass overrides `publishes(event)` for what goes out and `publisher_of(event)` for
where — a channel per event is the queue's `channel_of`, not another publisher.

| Rule | What it means |
|---|---|
| after the commit, never before | inside a unit of work the events wait for its `after_commit`, and a rollback drops them; outside one each `save` commits on its own, so they go out as it returns |
| taken once | what goes out is pulled from the aggregate and the rest recorded back, so a later save — or the Feature's own `pull_events()` — never hands the same event on twice |
| a record of its own | an event saved by itself goes out when it was never tried |
| the commit stands | a publisher that raises never undoes it: the error is logged and stamped |

**The stored row says how it went.** A `DeliverableEventMixin` event the repository stored is
stamped in a unit of work of its own: `delivered_at` and `delivery={"attempts": n}` when it went
out, `delivery["last_error"]` when it did not — at once for a publisher that sends in the call,
when the `Future` resolves for a forwarding one. `created_at` is when it happened, `delivered_at`
when it went out: both are there for an audit. Neither field is ever sent: `as_json()` leaves them
out.

**The mark is a default filter, not a condition.** `DeliverableEventMixin` says "a contract other
contexts hear" and is what `publish_on_commit` picks by default. `publisher.publish(...)` never
looks at it. On the explicit path — a Feature that pulls and publishes — the order, what a
rollback undoes and whether a failure matters are the Feature's to decide.

## Who guarantees what

| Stretch | Who answers for it |
|---|---|
| what to publish, and when | the Feature, or `publish_on_commit` |
| the publisher to the broker | the publisher — in the call, or its `Forwarding` |
| the broker to a consumer | the broker and the consumer's subscription: retries, dead letters, redelivery — [`subscribe`](brokers.md), [`QueueGateway`](../entrypoints/queue.md) |
| the consumer storing it as its own event | the consumer's context |

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
first publish, and reused. It is the same shape `BackgroundQueue` has always had, for a different
reason: there, the function runs in the worker because a bus cannot cross a process.

**Publishing runs the subscriber in the call**, so a subscriber that publishes re-enters the
queue depth-first — a listener registered after it hears the *inner* fact first. Where the order
of facts matters, write the fact down in the context it happened in, or publish through a
`Forwarding`, which sends its line in order (a message waiting for its retry holds back what
comes after it).

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
gets a translation in `entrypoints/`, never a second `record` in the domain. A stored event of an old
version is still its own class: the table stores the wire name and the payload. Historical
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
