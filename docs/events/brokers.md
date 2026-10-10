# Events over Kafka, RabbitMQ, Redis or NATS — `[faststream]`

The domain keeps two verbs: `publish(event)`, and registering an event on a bus. Which broker
carries it — a topic, a queue, a channel, a subject — and with which partitions, retries or
acknowledgements is the broker's configuration. [FastStream](https://faststream.ag2.ai) speaks to
each one with the same API, and `sincpro_framework.event_driven.adapters.faststream` sends and `sincpro_framework.entrypoints.adapters.faststream` listens:

- `FastStreamQueue(broker)` is one more `Queue`: `put` for synchronous code, `aput` for async.
- `subscribe(broker, Subscriber(...))` gives every event the buses registered its subscription.

```bash
pip install sincpro-framework[faststream] "faststream[kafka]"     # or [rabbit], [redis], [nats]
```

Every block on this page runs, in order, in `tests/docs/test_persistence_guide.py`, against
FastStream's in-memory test broker — the code is the same against a real one.

## The contexts, as always

```python
import asyncio
from dataclasses import dataclass

from sincpro_framework import Feature, UseFramework
from sincpro_framework.ddd.events import DomainEvent


@dataclass(kw_only=True)
class InvoicePaid(DomainEvent):
    name = "billing.v1.invoice_paid"
    amount: int = 0


accounting = UseFramework("accounting", log_after_execution=False)
booked: list[int] = []


@accounting.feature(InvoicePaid)
class BookPayment(Feature):
    def execute(self, dto: InvoicePaid) -> None:
        booked.append(dto.amount)
```

## Listening: the buses say what they hear

```python
from faststream.kafka import KafkaBroker, TestKafkaBroker

from sincpro_framework.event_driven import Publisher, Subscriber
from sincpro_framework.entrypoints.adapters.faststream import subscribe
from sincpro_framework.event_driven.adapters.faststream import FastStreamQueue, keyed_by_entity

broker = KafkaBroker("kafka:9092")
channels = subscribe(broker, Subscriber(accounting))
assert channels == ["billing.v1.invoice_paid"]
```

In a consumer process that is the whole program: `await FastStream(broker).run()`, or
`faststream run consumer:app`. A message is rebuilt as the class the receiving context declared
for that name and handed to its buses off the event loop, inside the trace that published it.
How each message is then settled is the framework's, not the broker's default — see below.

## Sending: synchronous code

`start()` gives the queue a loop of its own, in a daemon thread, with the broker connected on it:
`put` works from any synchronous caller and waits until the broker took the message, so an error
surfaces where the event was published. `keyed_by_entity` makes the entity Kafka's message key,
so one entity's events keep their order.

```python
async def a_synchronous_service() -> None:
    async with TestKafkaBroker(broker):                 # a real broker in a service
        queue = FastStreamQueue(broker, options_of=keyed_by_entity).start()
        publish = Publisher(queue).publish
        await asyncio.to_thread(publish, InvoicePaid(amount=120, entity_id="inv-1"))
        queue.stop()


asyncio.run(a_synchronous_service())
assert booked == [120]
```

## Sending: async code

A service that is already async and connected the broker itself — a FastAPI lifespan, a
FastStream app — uses the queue without starting it: `aput` publishes on the caller's loop.

```python
from sincpro_framework.event_driven import AsyncPublisher


async def an_async_service() -> None:
    async with TestKafkaBroker(broker):
        await AsyncPublisher(FastStreamQueue(broker)).publish(InvoicePaid(amount=80))


asyncio.run(an_async_service())
assert booked == [120, 80]
```

## Channels are yours to name

By default an event travels on the channel named after it. `channel_of` sends it elsewhere — one
topic for a whole context, say — and the event's wire name rides as the `sincpro-event` header,
so the subscribing side still routes each event to its class:

```python
shared = KafkaBroker("kafka:9092")
subscribe(shared, Subscriber(accounting), channel_of_name=lambda _name: "billing")


async def one_topic() -> None:
    async with TestKafkaBroker(shared):
        await FastStreamQueue(shared, channel_of=lambda _event: "billing").aput(InvoicePaid(amount=5))


asyncio.run(one_topic())
assert booked == [120, 80, 5]
```

## What is guaranteed, and what is not

Read this before relying on an event to keep two contexts in step.

- **Sending.** `put` returns once the broker took the message, so a broker that is down fails
  the `publish` where it was called. What the broker then promises — kept on disk, replicated —
  is its configuration (a Kafka topic's replication, a durable RabbitMQ queue).
- **Between the commit and the publish.** A Feature that saves and then publishes can crash in
  between: the state is committed and the event is lost. The context's event table closes that
  window: the event is kept in the same transaction as the change, and an `EventRelay` publishes
  it after, at least once
  ([persistence guide §12](../persistence/guide.md#12-delivering-events)).
- **Receiving: at least once.** `subscribe` is the queue entrypoint over the subscriber's buses
  — [`QueueGateway`](../entrypoints/queue.md), their events only — so every subscription
  acknowledges manually, never with FastStream's per-broker default (which commits a Kafka offset
  *before* the handler runs and discards a failed RabbitMQ message: at most once). Each message is
  settled from what happened:

  | What happened | Settled | So the broker |
  |---|---|---|
  | every bus handled the event | ack | forgets it |
  | a redelivery the inbox saw completed | ack, no bus run again | forgets it |
  | a bus raised | retried: a nack (RabbitMQ, NATS) or a copy with the attempt counted (Kafka, where a nack blocks the partition) | redelivers it, up to `max_attempts` (5), then dead-letters it with the reason |
  | the payload is not the event it claims to be | dead-lettered at once, logged | never redelivers it: RabbitMQ's own dead-letter exchange, `{channel}.dlq` elsewhere |
  | no bus here registered that event (a shared channel) | ack | leaves it to the consumer it belongs to |

  Every bus that registered an event runs it from one subscription; a retry re-runs only the
  bus that failed — the inbox remembers the others (in this process by default:
  `options=QueueOptions(inbox=KeyValueRecords(RedisKeyValue(...)))` across replicas). A handler
  should still be idempotent: the inbox forgets after `keep_for`, and a crash between the effect
  and the ack runs it again. On RabbitMQ the attempts are counted by a quorum queue's delivery
  count; a classic queue has none, so give it a delivery limit or retry by copy. Commands consumed
  from a broker, and who may send them, are `QueueGateway`'s.
- **RabbitMQ, several services on one event.** Every consumer group subscribes the queue named
  after the channel, so two services hearing the same event compete for it — see
  [queue.md](../entrypoints/queue.md) for the exchange-per-group topology until it is the default.
- **Redis Pub/Sub and core NATS** have no acknowledgement at all: there delivery stays at most
  once whatever is settled — use Redis Streams or NATS JetStream when a lost event matters.
- **Order.** Kafka keeps one key's messages in order — `keyed_by_entity` makes that key the
  entity; across keys, and on brokers without partitions, there is no order to rely on.
- **Unknown events.** On a channel several events share, one no bus here registered is skipped:
  it is another consumer's.
- `BackgroundQueue`, the in-process default, holds events in memory: they are lost when the
  process stops before its worker handled them — at most once.

| | |
|---|---|
| `FastStreamQueue(broker, channel_of=by_event_name, options_of=None)` | a `Queue`; `start()` / `stop()` for synchronous code |
| `queue.put(event)` / `await queue.aput(event)` | publish and wait until the broker took it |
| `keyed_by_entity` | Kafka: `entity_id` as the message key |
| `subscribe(broker, subscriber, channel_of_name=by_name, options=None)` | one subscription per channel of the events the buses registered — `QueueGateway` for events, `options` a `QueueOptions` |
| `EVENT_HEADER` | `sincpro-event`: the wire name beside the payload |
