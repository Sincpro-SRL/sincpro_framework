# Queue entrypoint — `QueueGateway`

The queue **entrypoint**: what other services may make this process do through Kafka, RabbitMQ,
Redis or NATS. It sits on FastStream (`[faststream]` + the broker driver). It is **not** the events
module: `Publisher`/`FastStreamQueue` are how a fact leaves (`sincpro-framework-domain-events`);
this is what you use when a broker is also a door into your Commands. Full depth:
`docs/entrypoints/queue.md`.

```python
from faststream.kafka import KafkaBroker
from sincpro_framework.entrypoints.faststream import QueueGateway

broker = KafkaBroker("kafka:9092")
gateway = QueueGateway(broker, [billing, accounting])
gateway.build()                          # events heard, declared Commands consumed
app = FastStream(broker)                 # `faststream run app:app`
```

## What is consumed

| Kind | Declared | Semantics |
|---|---|---|
| a `DomainEvent` | nothing; `@queue.hears(group=, channel=)` to move it | pub/sub on the event's wire name, each context in its own consumer group |
| a Command | `@queue.consumes(channel, producers=, max_attempts=, concurrency=)` | point-to-point: exactly one handler per channel; no answer — its outcome is a verdict |

```python
from sincpro_framework.entrypoints.exposure import queue

@accounting.feature(InvoiceIssued)              # heard — nothing to declare
class Book(Feature): ...

@billing.feature(CommandIssueInvoice)
@queue.consumes("billing.invoices.issue", producers=("urn:svc:sales",), max_attempts=5)
class IssueInvoice(Feature): ...
```

- **Every registered event is heard**; a Command is never consumed without a declaration.
- A Command must say who may send it: `producers=`, an `@auth.requires`/`@auth.public` on a guarded
  bus, or `unguarded=True`.
- The build refuses two handlers on one channel, a channel carrying both commands and events,
  `max_attempts`/`concurrency` below 1, `time_limit` not below the inbox's `in_progress_for`.

## Settling — a verdict per delivery

Every subscription is `AckPolicy.MANUAL` (never FastStream's per-broker default, which is at most
once). Each delivery gets a verdict from the shared failure classification:

| What happened | Verdict |
|---|---|
| the bus handled it | `ACK` |
| a redelivery the inbox saw complete | `REPLAY` (ack, bus does not run) |
| an event nobody here handles, on a shared channel | `SKIP` (ack) |
| conflict, in progress, exhausted, unavailable, internal, time limit | `RETRY` (nack or counted copy; dead-letter at `max_attempts`) |
| undecodable, invalid, unauthenticated, permission denied, producer not allowed, not found, key reused, domain, expired | `DEAD_LETTER` |

- Retry/dead-letter mechanics are per broker (Kafka republishes; RabbitMQ nacks/rejects; Redis/NATS
  copy to `{channel}.dlq`). A copy that cannot be published ⇒ the delivery is **nacked**: nothing is
  acked that was not written somewhere.
- **RabbitMQ classic queues have no delivery count** — use a quorum queue or
  `retry_by=RetryBy.REPUBLISH`. On RabbitMQ two *services* on one event compete for it (Kafka/NATS
  give each group a copy natively).
- **Redis Pub/Sub and core NATS have no acknowledgement** — at most once.

## The inbox: at least once, run once

Before the bus runs, the delivery is claimed as `inbox:{context.Command}:{source}:{id}` in an
`IdempotencyRecords` (in memory by default; `KeyValueRecords(RedisKeyValue(...))` across replicas).
A redelivery of a completed message is acked without running; the same source+id with another body
is dead-lettered `KEY_REUSED`. `@idempotency.once` keys the **business request**; the inbox keys the
**delivery** — two stages on the same port.

## CloudEvents and the producer

Binary-mode CloudEvents 1.0: `ce_id`, `ce_source`, `ce_type`, `ce_subject`, `ce_time`,
`ce_correlationid`, `ce_causationid`, plus `traceparent`. A producer stamps them:

```python
from sincpro_framework.entrypoints.faststream import cloud_event_headers
headers = cloud_event_headers(command, source="urn:svc:sales", id=request_id)
await broker.publish(command.model_dump_json().encode(), "billing.invoices.issue", headers=headers)
```

`producers=` is checked **before** the message is decoded and the bus runs; a disallowed or unnamed
producer is dead-lettered `PRODUCER_NOT_ALLOWED`. `ce_source` is only trustworthy inside one trust
boundary — on RabbitMQ use `QueueOptions(identify=rabbit_user_id)`.

## Reference

| | |
|---|---|
| `QueueGateway(broker, buses, options=, exposure=, unguarded=, port=)` | `wire = "queue"` |
| `gateway.build()` / `consume(message, as_=, decode=, channel=)` | subscriptions / full control (answers a `Verdict`) |
| `gateway.surface()`, `verify()`, `manifest()`, `asyncapi()` | the surface; AsyncAPI 3.0 |
| `QueueOptions(inbox, max_attempts, retry_by, dead_letter_by, dead_letter_suffix, time_limit, in_progress_for, keep_for, expires_after, identify, subscription_of)` | how the wire settles |
| `cloud_event_headers(...)`, `Envelope.of(headers)`, `Verdict`, `Settlement` | the envelope and verdict |
