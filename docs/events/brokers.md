# Events over Kafka, RabbitMQ, Redis or NATS — `[faststream]`

The domain keeps two verbs: `publish(event)`, and registering an event on a bus. Which broker
carries it — a topic, a queue, a channel, a subject — and with which partitions, retries or
acknowledgements is the broker's configuration. [FastStream](https://faststream.ag2.ai) speaks to
each one with the same API, and `sincpro_framework.events.faststream` plugs it in on both sides:

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

from sincpro_framework.events import Publisher, Subscriber
from sincpro_framework.events.faststream import FastStreamQueue, keyed_by_entity, subscribe

broker = KafkaBroker("kafka:9092")
channels = subscribe(broker, Subscriber(accounting))
assert channels == ["billing.v1.invoice_paid"]
```

In a consumer process that is the whole program: `await FastStream(broker).run()`, or
`faststream run consumer:app`. A message is rebuilt as the class the receiving context declared
for that name and handed to its buses off the event loop, inside the trace that published it; a
handler that raises is the broker's to retry or dead-letter.

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
from sincpro_framework.events import AsyncPublisher


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
  between: the state is committed and the event is lost. Nothing here closes that window yet —
  that is an outbox (the event written in the same transaction, sent by a relay).
- **Receiving.** A handler that raises leaves the message to the broker, which redelivers it as
  it is configured to: **at least once**, so a handler must be idempotent — the same event twice
  leaves the same state. A message that can never be handled (a payload of another shape) is
  redelivered until the broker moves it aside: configure a dead-letter queue on the broker.
- **Order.** Kafka keeps one key's messages in order — `keyed_by_entity` makes that key the
  entity; across keys, and on brokers without partitions, there is no order to rely on.
- **Unknown events.** On a channel several events share, one no bus here registered is skipped:
  it is another consumer's.
- `BackgroundQueue`, the in-process default, holds events in memory: they are lost when the
  process stops before its worker handled them — at most once.

| | |
|---|---|
| `FastStreamQueue(broker, channel_of=by_event_name, options_of=no_options)` | a `Queue`; `start()` / `stop()` for synchronous code |
| `queue.put(event)` / `await queue.aput(event)` | publish and wait until the broker took it |
| `keyed_by_entity` | Kafka: `entity_id` as the message key |
| `subscribe(broker, subscriber, channel_of_name=by_name)` | one subscription per channel of the events the buses registered |
| `EVENT_HEADER` | `sincpro-event`: the wire name beside the payload |
