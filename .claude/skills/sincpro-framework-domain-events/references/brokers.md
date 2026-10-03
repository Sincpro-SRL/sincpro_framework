# Brokers — Kafka, RabbitMQ, Redis, NATS (`[faststream]`)

The domain keeps two verbs: `publish(event)` and registering an event on a bus. Which broker carries
it, with which partitions/retries/acknowledgements, is the broker's configuration. Sending lives in
`sincpro_framework.event_driven.adapters.faststream`; listening is an entrypoint in
`sincpro_framework.entrypoints.faststream`.

```bash
pip install sincpro-framework[faststream] "faststream[kafka]"     # or [rabbit], [redis], [nats]
```

Deeper, in the framework repo: `docs/events/brokers.md` and `docs/entrypoints/queue.md`.

```python
from faststream.kafka import KafkaBroker              # or RabbitBroker, RedisBroker, NatsBroker

from sincpro_framework.event_driven import Publisher, Subscriber
from sincpro_framework.event_driven.adapters.faststream import FastStreamQueue, keyed_by_entity
from sincpro_framework.entrypoints.faststream import subscribe

broker = KafkaBroker("kafka:9092")

# Listening — the buses say what they hear
channels = subscribe(broker, Subscriber(accounting))     # ["billing.v1.invoice_paid"]

# A consumer process is: await FastStream(broker).run(), or `faststream run consumer:app`

# Sending from synchronous code
queue = FastStreamQueue(broker, options_of=keyed_by_entity).start()
Publisher(queue).publish(InvoicePaid(amount=120, entity_id="inv-1"))
queue.stop()

# Sending from async code (the caller owns the loop)
from sincpro_framework.event_driven import AsyncPublisher
await AsyncPublisher(FastStreamQueue(broker)).publish(InvoicePaid(amount=80))
```

| | |
|---|---|
| `FastStreamQueue(broker, channel_of=by_event_name, options_of=no_options)` | a `Queue`; `start()`/`stop()` for sync code |
| `queue.put(event)` / `await queue.aput(event)` | publish and wait until the broker took it |
| `keyed_by_entity` | Kafka: `entity_id` as the message key — one entity's events keep their order |
| `subscribe(broker, subscriber, channel_of_name=by_name, options=None)` | one subscription per channel of the events the buses registered |
| `EVENT_HEADER` (`sincpro-event`) | the wire name beside the payload |

## Channels are yours to name — on both sides

By default an event travels on the channel named after it (`by_event_name`). `channel_of` on the
queue sends it elsewhere (one topic per context, say); the consumer must say the same with
`channel_of_name`, or it subscribes to channels nobody writes to and hears nothing:

```python
subscribe(broker, Subscriber(accounting), channel_of_name=lambda _name: "billing")
FastStreamQueue(broker, channel_of=lambda _event: "billing")
```

## Across replicas: the inbox

```python
from sincpro_framework.caching import KeyValueRecords
from sincpro_framework.caching.adapters.redis import RedisKeyValue          # [redis]
from sincpro_framework.entrypoints.faststream import QueueOptions

subscribe(broker, Subscriber(accounting),
          options=QueueOptions(inbox=KeyValueRecords(RedisKeyValue(redis_client)), max_attempts=5))
```

The default inbox is in memory: it remembers which bus already handled a redelivered message in
this replica only.

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
- **RabbitMQ: two services hearing one event compete for it.** Every consumer group subscribes the
  queue named after the channel, so each service gets only part of the events. Kafka (`group_id`)
  and NATS (`queue=`) give each group its own copy; on RabbitMQ declare a queue per group bound to
  an exchange with `QueueOptions(subscription_of=...)` (`sincpro-framework-entrypoints`).
- **Failures**: a bus that raises is retried up to `max_attempts` (5), then dead-lettered
  (RabbitMQ's dead-letter exchange, `{channel}.dlq` elsewhere); a payload that is not the event it
  claims is dead-lettered at once; an event no bus here registered is acknowledged and skipped.
- `BackgroundQueue`, the in-process queue, holds events in memory — lost if the process stops
  before its worker handled them.

Consuming **Commands** from a broker, and who may send them, is `QueueGateway`
(`sincpro-framework-entrypoints`), not this.
