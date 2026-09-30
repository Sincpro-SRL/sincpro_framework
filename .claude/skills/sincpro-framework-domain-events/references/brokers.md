# Brokers — Kafka, RabbitMQ, Redis, NATS (`[faststream]`)

The domain keeps two verbs: `publish(event)` and registering an event on a bus. Which broker carries
it, with which partitions/retries/acknowledgements, is the broker's configuration. FastStream speaks
to each with one API, and `sincpro_framework.events.faststream` plugs it in on both sides.

```bash
pip install sincpro-framework[faststream] "faststream[kafka]"     # or [rabbit], [redis], [nats]
```

Full depth: `docs/events/brokers.md`.

```python
from sincpro_framework.events import Publisher, Subscriber
from sincpro_framework.events.faststream import FastStreamQueue, keyed_by_entity, subscribe

broker = KafkaBroker("kafka:9092")

# Listening — the buses say what they hear
channels = subscribe(broker, Subscriber(accounting))     # ["billing.v1.invoice_paid"]

# A consumer process is: await FastStream(broker).run(), or `faststream run consumer:app`

# Sending from synchronous code
queue = FastStreamQueue(broker, options_of=keyed_by_entity).start()
Publisher(queue).publish(InvoicePaid(amount=120, entity_id="inv-1"))
queue.stop()

# Sending from async code (the caller owns the loop)
from sincpro_framework.events import AsyncPublisher
await AsyncPublisher(FastStreamQueue(broker)).publish(InvoicePaid(amount=80))
```

| | |
|---|---|
| `FastStreamQueue(broker, channel_of=by_event_name, options_of=no_options)` | a `Queue`; `start()`/`stop()` for sync code |
| `queue.put(event)` / `await queue.aput(event)` | publish and wait until the broker took it |
| `keyed_by_entity` | Kafka: `entity_id` as the message key — one entity's events keep their order |
| `subscribe(broker, subscriber, channel_of_name=by_name, options=None)` | one subscription per channel of the events the buses registered |
| `EVENT_HEADER` (`sincpro-event`) | the wire name beside the payload |

## What is guaranteed — read before relying on it

- **Sending.** `put` returns once the broker took the message, so a broker that is down fails the
  `publish` where it was called. What the broker then promises (disk, replication) is its config.
- **Between commit and publish.** A Feature that saves then publishes can crash in between: state
  committed, event lost. Nothing here closes that window — that is the **outbox**.
- **Receiving: at least once.** `subscribe` is `QueueGateway` over the subscriber's buses, and every
  subscription acknowledges manually (never FastStream's per-broker default, which is at-most-once).
  Every bus that registered the event runs it from one subscription; a retry re-runs only the bus
  that failed (an inbox remembers the others; across replicas, give the queue a Redis-backed
  `QueueOptions(inbox=...)`). A handler should still be idempotent.
- **Redis Pub/Sub and core NATS have no acknowledgement** — at most once. Use Redis Streams or NATS
  JetStream when a lost event matters.
- **Order** is per key on Kafka (`keyed_by_entity`); elsewhere there is none to rely on.
- `BackgroundQueue`, the in-process default, holds events in memory — lost if the process stops
  before its worker handled them.

Consuming **Commands** from a broker, and who may send them, is `QueueGateway`
(`docs/entrypoints/queue.md`), not this.
