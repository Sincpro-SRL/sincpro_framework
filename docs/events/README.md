# Events: a publisher, a queue, and a subscriber made of buses

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

Nothing is wired by default and nothing is stored. A project builds the queue it wants with the
buses it names, and publishes when a Feature decides to. What an aggregate recorded lives in
memory and dies with the object.

## The pieces

| Piece | Module | What it is |
|---|---|---|
| `DomainEvent` | `ddd/events.py` | An `Entity` with the envelope: `id` (UUID v7), `created_at`, `entity_type`, `entity_id`, `correlation_id`, `causation_id`, `sequence`, `label` |
| `Entity.record` / `pull_events` | `ddd/entity/entity.py` | Records with the aggregate's type, id and sequence; pulling hands them back and forgets them |
| `ChangeTrackingMixin` | `ddd/entity/mixins/tracking.py` | One `Updated` event with every field that changed — see [change-tracking.md](change-tracking.md) |
| `Publisher` | `events/publisher.py` | Emits to a queue, with the signature of a bus |
| `Subscriber` | `events/subscriber.py` | The bus instances handed in explicitly; executes the ones whose registry knows the event |
| `Queue` | `events/queue.py` | The protocol: `put` and `aput` |
| `SyncQueue` | `events/queue.py` | Same process; `publish` runs the subscriber and returns |
| `BackgroundQueue` | `events/queue.py` | Another process consumes; `start()` / `stop()` |

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
The event crosses as its class name and JSON and is rebuilt as the class the subscriber knows.
`stop()` sends the stop signal and waits for the worker.

Kafka, RabbitMQ and Redis are each one more `Queue`: `put`, `aput`, and whatever `start` / `stop`
the transport needs.
