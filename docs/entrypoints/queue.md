# Queues — Commands and events from a broker, on FastStream

`QueueGateway` is the queue **entrypoint**: what other services may make this process do through
Kafka, RabbitMQ, Redis or NATS. It sits on FastStream (`[faststream]` extra plus the broker's own
driver, e.g. `faststream[kafka]`) and adds what a public consumer needs: declared exposure, the
producer's identity, an inbox, explicit settling, dead letters, CloudEvents and AsyncAPI.

It is not the events module. `Publisher` and `FastStreamQueue` ([events/brokers.md](../events/brokers.md))
stay how a fact **leaves** a process. `subscribe()` stays the one-line way to hear events — it is
this gateway over a `Subscriber`'s buses, their events only, so both settle the same way. The
gateway is what you use when a broker is also a door into your Commands.

```text
from faststream.kafka import KafkaBroker
from sincpro_framework.entrypoints.faststream import QueueGateway

broker = KafkaBroker("kafka:9092")
gateway = QueueGateway(broker, [billing, accounting])
gateway.build()                        # every registered event heard, declared Commands consumed
app = FastStream(broker)               # `faststream run app:app`
```

## What is consumed

An event needs nothing: registering a Feature for it is already saying the context hears it. A
Command says it is on the queue, and who may send it:

```text
from sincpro_framework.entrypoints.exposure import queue

@accounting.feature(InvoiceIssued)                 # heard — nothing to declare
class Book(Feature): ...

@audit.feature(InvoiceIssued)
@queue.hears(group="audit-replay")                 # only to move it to another group or channel
class Replay(Feature): ...

@billing.feature(CommandIssueInvoice)
@queue.consumes("billing.invoices.issue", producers=("urn:svc:sales",), max_attempts=5)
class IssueInvoice(Feature): ...
```

| Kind | Declared | Semantics |
|---|---|---|
| a DomainEvent | nothing; `@queue.hears(group=, channel=)` to move it | publish/subscribe on the event's wire name; each context in its own consumer group |
| a Command | `@queue.consumes(channel, producers=, max_attempts=, concurrency=)` | point to point: **exactly one** handler per channel; no answer — its outcome is a verdict |

- **Every registered event is heard**, in either exposure. It reacts to what already happened, so
  it declares no access: the access rule below is for Commands.
- **A Command is never consumed without a declaration**, in either exposure: accepting any Command
  from a broker is an unintended public API.
- **Contexts of this process hearing one event in one group are one subscription** that runs each
  of them, each handed the event as the class it declared for that name — two subscriptions on
  one RabbitMQ queue would compete for the message. It is acked when every context ran; a retry
  re-runs only the one that failed (the inbox keys each context apart).
- `gateway.group(bus, prefix="staging.", namespace="ledger")`: `prefix` goes in front of every
  channel of that context, and `namespace` is its consumer group. Without it, the group is the
  alias. A binding's own `group=` wins.
- `gateway.bind(Command, QueueBinding(kind="consumes", channel=...))` and
  `gateway.override(Command, max_attempts=...)` shape the binding in the composition, as on every
  wire. `gateway.manifest()` lists the surface for a CI snapshot.

The build refuses the surface, giving every reason at once, when:

- two handlers consume one command channel,
- a command channel also carries events,
- `max_attempts` or `concurrency` is below 1,
- `time_limit` is not below the inbox's `in_progress_for`,
- a Command does not say who may send it: it needs `producers=`, an `@auth.requires` or
  `@auth.public` on a guarded bus, or `unguarded=True` on the gateway. A guarded bus with
  nothing declared is refused even with `producers=`, because the bus itself would refuse the call.

## Settling: a verdict per delivery

Every subscription uses `AckPolicy.MANUAL`. FastStream's per-broker defaults (`ACK_FIRST` on
Kafka, `REJECT_ON_ERROR` elsewhere) are at most once and are never used. Each delivery gets a
verdict, from the failure classification every wire shares (`transport.failures`):

| What happened | Verdict | Settled |
|---|---|---|
| the bus handled it | `ACK` | ack |
| a redelivery of a message the inbox saw complete | `REPLAY` | ack; the bus does not run |
| an event nobody here handles, on a shared channel | `SKIP` | ack |
| conflict, in progress, exhausted, unavailable, internal, time limit | `RETRY` | nack, or a counted copy (below); dead-letter at `max_attempts` |
| undecodable, invalid DTO, unauthenticated, permission denied, producer not allowed, not found, key reused, domain error, expired | `DEAD_LETTER` | the dead-letter channel, or the broker's own |

Unauthenticated, permission-denied and producer-not-allowed refusals are also logged as security
events. The reason is a stable UPPER_SNAKE string: the error's class when the producer may read it
(`DOMAIN_ERROR`, `INVOICE_NOT_FOUND`), the kind otherwise (`INTERNAL`), or the gateway's own
(`PRODUCER_NOT_ALLOWED`, `UNDECODABLE`, `EXPIRED`, `KEY_REUSED`, `IN_PROGRESS`, `TIME_LIMIT`).

### Retries and dead letters, per broker

| Broker | Consumer group | Attempts counted by | Retry by default | Dead letter by default |
|---|---|---|---|---|
| Kafka | `group_id` | the `sincpro-attempts` header | **republish**: a copy with the attempt counted, then ack (a nack would re-read the offset and block the partition) | a copy to `{channel}.dlq`, then ack |
| RabbitMQ | the queue | `x-delivery-count` (quorum queues) | **nack**: the broker redelivers | **reject**: the queue's dead-letter exchange keeps it |
| Redis | none (Pub/Sub) | the `sincpro-attempts` header | republish | a copy to `{channel}.dlq` |
| NATS | queue group | JetStream's `num_delivered` | nack | a copy to `{channel}.dlq` |

- The attempt count is the larger of the broker's own count and the `sincpro-attempts` header. At
  `max_attempts` (the binding's value, else `QueueOptions.max_attempts`, default 5), a retry becomes
  a dead letter.
- A copy on `{channel}.dlq` keeps the body and the CloudEvents headers, and adds
  `sincpro-dead-letter-reason`, `sincpro-dead-letter-kind`, `sincpro-dead-letter-from` and
  `sincpro-attempts`.
- If a copy (dead letter or retry) cannot be published, the delivery is **nacked**. Nothing is
  acked that was not written somewhere.
- `QueueOptions(retry_by=RetryBy.NACK | RetryBy.REPUBLISH, dead_letter_by=DeadLetterBy.NATIVE |
  DeadLetterBy.CHANNEL, dead_letter_suffix=".dlq")` overrides the defaults.
- **RabbitMQ classic queues have no delivery count.** With the default nack, they redeliver
  without limit. Use a quorum queue (via `subscription_of`, below) or `retry_by=RetryBy.REPUBLISH`.
  Native dead-lettering needs the queue to have a dead-letter exchange. With
  `DeadLetterBy.NATIVE` the reason is only logged; RabbitMQ adds its own `x-death` headers.
- **RabbitMQ: two services hearing one event compete for it.** Kafka (`group_id`) and NATS
  (`queue=`) give each consumer group its own copy natively; on RabbitMQ every group subscribes
  the queue named after the channel, so two *services* on one event each get part of the events.
  One service per event, and the replicas of one service, work as documented. For several
  services on one event, declare a queue per group bound to an exchange with `subscription_of`
  (below) and publish to that exchange. The framework does it by default in a later minor.
- **Redis Pub/Sub has no acknowledgement**, so delivery there stays at most once whatever is
  settled. The same holds for core NATS.
- A counted copy goes to the **back** of the channel, so per-key order does not hold across a
  retry.
- Retries run **without delay** in this release. Delayed retry topics are phase 2.

## The inbox: at least once, run once

Before the bus runs, the delivery is claimed as `inbox:{context.Command}:{source}:{id}` in an
`IdempotencyRecords`:

- A redelivery of a completed message is acked **without running**.
- One that is still running elsewhere is retried.
- The same source and id with another body is dead-lettered as `KEY_REUSED`.
- A failure releases the claim, so the retry runs.
- A time-limit timeout keeps the claim, because the first run may still be going. A redelivery
  then waits for the claim to expire.

```text
from sincpro_framework.caching import KeyValueRecords, RedisKeyValue
QueueOptions(inbox=KeyValueRecords(RedisKeyValue(...)))   # shared across replicas
QueueOptions(inbox=None)                                  # off
```

The default is in memory, which covers one replica. The inbox keys the **delivery** in the wire.
`@idempotency.once` keys the **business request** in the bus. Both sit on the same port as two
stages. On records that commit inside the use case's own transaction, effects are exactly once
within that database. On a key-value store they are best effort: a replica that dies between the
use case and `complete` runs the message again once the claim expires.

The id is the CloudEvents `id`, else a DomainEvent's own `id`, else FastStream's message id. So
events published by `FastStreamQueue` (which writes no envelope) are still run once.

### Time limits, TTL, order

- `QueueOptions(time_limit=timedelta(seconds=30))`: past it, the delivery is retried. It **must**
  stay below `in_progress_for` (the build refuses otherwise) and below the broker's visibility
  timeout or `max.poll.interval.ms`. The gateway cannot see that last value, so it does not check it.
- `QueueOptions(expires_after=timedelta(hours=1))`: a message whose `ce_time` is older is
  dead-lettered as `EXPIRED`.
- Order holds per key only, and only with a concurrency of one per key. On Kafka, `concurrency=`
  becomes `max_workers` in the consumer group, which runs partitions concurrently and each key in
  order.

## CloudEvents and the producer's identity

The envelope uses CloudEvents 1.0 **binary mode**: the attributes travel in headers and the body
stays the payload. The gateway writes `ce_id`, `ce_source`, `ce_type`, `ce_subject`, `ce_time`,
`ce_correlationid`, `ce_causationid`, `ce_specversion`, plus `traceparent` / `tracestate`. It reads
the prefixes `ce_` (Kafka binding), `ce-` (HTTP) and `cloudEvents:` / `cloudEvents_` (AMQP). A
producer stamps them with:

```text
from sincpro_framework.entrypoints.faststream import cloud_event_headers
headers = cloud_event_headers(command, source="urn:svc:sales", id=request_id)
await broker.publish(command.model_dump_json().encode(), "billing.invoices.issue", headers=headers)
```

`producers=` is checked against the producer's identity **before** the message is decoded and
before the bus runs. A producer that is not allowed, or a message that names no producer, is
dead-lettered as `PRODUCER_NOT_ALLOWED`. By default the identity is `ce_source`, which is only
trustworthy inside one trust boundary, because anyone who can publish can write that header.
On RabbitMQ, use the broker-validated `user-id`:

```text
QueueOptions(identify=rabbit_user_id)
```

Signed envelopes are phase 2. The message's headers also reach the bus's `AccessControl` as
`Credentials(transport="queue")`, in the same worker thread as the bus call, so `@auth.requires`
works against a service identity a provider reads from them.

The bus runs inside the producer's trace (`traceparent`), as a child of it.

## Full control

A subscriber of your own runs the same path: producer check, decode, inbox, bus, verdict,
settlement.

```text
@broker.subscriber("legacy.topic", ack_policy=AckPolicy.MANUAL)
async def legacy(message: StreamMessage = Context("message")) -> None:
    await gateway.consume(message, as_=CommandIssueInvoice, decode=legacy_decoder)
```

`decode` gets the message and returns the DTO or the fields it validates from. `consume` settles
the message and returns the `Verdict`. The Command must be answered by a bus of the gateway:

- If it is on the surface, its binding (producers, attempts) applies.
- If not, the derived binding applies under the same access rule.

To declare a subscription the defaults do not cover (a RabbitMQ quorum queue, an exchange per
consumer group, a Redis Stream, a JetStream stream), pass `subscription_of`:

```text
def quorum(broker, channel, group, concurrency):
    return broker.subscriber(RabbitQueue(channel, durable=True, queue_type=QueueType.QUORUM),
                             ack_policy=AckPolicy.MANUAL)

QueueGateway(broker, [billing], options=QueueOptions(subscription_of=quorum))
```

## AsyncAPI

`gateway.asyncapi()` returns the AsyncAPI 3.0 document that FastStream generates for the broker.
Each subscription the gateway built appears there as a channel with a `receive` operation.

**Not built:**

- the message payload schema per Command (the subscriber reads the raw message, so FastStream
  has no annotation to derive it from),
- validation against the AsyncAPI meta-schema.

## Reference

| | |
|---|---|
| `QueueGateway(broker, buses, options=, exposure=, unguarded=, port=)` | `wire = "queue"`; its port is a `QueueWire` |
| `gateway.build()` | the `Subscription`s registered; a second call registers nothing new |
| `await gateway.consume(message, as_=, decode=, channel=)` | full control; answers a `Verdict` |
| `gateway.surface()`, `verify()`, `manifest()`, `asyncapi()` | as on every wire |
| `QueueOptions(inbox, max_attempts, retry_by, dead_letter_by, dead_letter_suffix, time_limit, in_progress_for, keep_for, expires_after, identify, subscription_of)` | how the wire settles |
| `cloud_event_headers(message, source=, type=, id=, subject=)`, `Envelope.of(headers)` | the envelope |
| `Verdict(settlement, kind, reason, attempts, command)`, `Settlement` | the verdict |
