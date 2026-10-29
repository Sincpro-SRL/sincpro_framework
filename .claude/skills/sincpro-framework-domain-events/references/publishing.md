# Publishing: the Publisher, forwarding, and publishing on commit

Use this when a fact leaves the Feature: to buses in the same process, or to a broker. Three
pieces, each opted into: a `Publisher` puts any message into a queue; `forwarding=` holds the
messages in a line in memory and sends them in order while the broker is down; a repository's
`publish_on_commit=` hands what a write recorded to a publisher once it committed and stamps the
stored event with how its delivery went. Event storing (the context's event table) and publishing
are separate concepts: the table records the facts in the commit, the publisher sends them.

## The Publisher: any message

```python
from sincpro_framework.event_driven import Publisher, Subscriber, SyncQueue

publisher = Publisher(SyncQueue(Subscriber(shipping)))
publisher.publish(event)                          # None: launch and forget
publisher.publish(event, ResponseShipment)        # the one answer — SyncQueue, exactly one bus
await publisher.get_async_publisher().publish(event)
```

- **It refuses nothing.** A `DomainEvent` is the recommended message: published inside an
  execution it goes out as a copy caused by it and in its flow (`causation_id`,
  `correlation_id`). A message of the project's own goes in as it is.
- **What a queue can carry is the queue's to say.** `FastStreamQueue` writes a `DomainEvent` or a
  `DataTransferObject` as its own JSON (`as_json`, the default `serializer=`); anything else needs
  `serializer=`, and `channel_of=` when it has no `name` — otherwise a `ProgrammingError` names
  the missing option. The AWS queues (`SnsQueue`, `SqsQueue`, `EventBridgeQueue`) write
  `DomainEvent`s only.

```python
import json

from sincpro_framework.event_driven.adapters.faststream import FastStreamQueue

tickets = FastStreamQueue(broker, channel_of=lambda message: "tickets", serializer=json.dumps)
Publisher(tickets.start()).publish({"ticket": "T-1"})
```

- **The typed publish** holds only where somebody answers in the same call: a `SyncQueue` with
  exactly one bus for that event. Anything else is refused, never guessed.

## Forwarding: store-and-forward in the process

`Publisher(queue)` sends in the call: a broker that is down fails the `publish` where it was
called. With `forwarding`, `publish` returns at once with a `Future[Forwarded]`:

```python
from datetime import timedelta

from sincpro_framework.circuit_breaker import CircuitBreaker
from sincpro_framework.event_driven import Forwarded, Forwarding, ForwardingHook, Publisher

rabbit = Publisher(queue, forwarding=True)            # the defaults
future = rabbit.publish(InvoicePaid(invoice_id="F-1"))
future.result()                                       # Forwarded(attempts=1, delivered_at=…)


class Alerted(ForwardingHook):
    def after_failure(self, message, forwarded: Forwarded) -> None:
        if forwarded.set_aside:
            alerts.send(f"{message.name}: {forwarded.last_error}")


rabbit = Publisher(
    queue,
    forwarding=Forwarding(
        name="rabbit",
        breaker=CircuitBreaker("rabbit", failures=3),
        hooks=[Alerted()],
        wait=timedelta(seconds=1),          # first pause, doubled each failed attempt
        longest_wait=timedelta(minutes=1),  # never longer
    ),
)
```

| What | How it behaves |
|---|---|
| the line | each message goes to the end of a line in memory; `publish` returns a `Future[Forwarded]` |
| one thread | one daemon thread per `Forwarding`, started by the first message, sends one at a time, in order — no cron, no worker process to deploy |
| a failed attempt | the message stays first; the thread sleeps the backoff (from `wait`, doubled, up to `longest_wait`) or the error's `retry_after`, whichever is longer |
| a broker that keeps failing | attempts go through a `CircuitBreaker` stored in the context's store: replicas sharing a store open and close together |
| a message that never goes | a `PERMANENT` error (invalid, a refusal) sets it aside — `Forwarded(set_aside=True)` — so it never blocks the line |
| its context | each message is sent in the contextvars context it was published in: trace, execution, tenant |
| hooks | `ForwardingHook.after_delivery(message, forwarded)` / `after_failure(message, forwarded)`, both optional, run on the forwarding thread; one that raises is logged |

- `Forwarded(attempts, delivered_at, last_error, set_aside)` is how one message went.
- A typed publish through a forwarding (`publish(event, Response)`) raises `ProgrammingError`:
  it returns before the message went out.
- One `Forwarding` forwards to one queue; give each queue its own.
- **Lost with the process.** The line lives in process memory: a message still waiting when the
  process dies is not sent. A fact that must survive that is stored in the event table, whose row
  says whether it went out — the framework ships no outbox that sends it again.

## Publishing on commit

Opt in per repository — where each bounded context wires its store:

```python
from sincpro_framework.data_layer.orm import Repository
from sincpro_framework.ddd.repositories import PublishOnCommit

Repository(database, billing_hooks, publish_on_commit=rabbit)           # every deliverable event
Repository(
    database,
    publish_on_commit=PublishOnCommit(
        rabbit, entities=[Invoice, Payment], when=lambda event: event.entity_id != ""
    ),
)


class InvoiceEvents(PublishOnCommit):                 # the advanced step
    def publishes(self, event) -> bool:               # what goes out
        return isinstance(event, InvoiceSent)

    def publisher_of(self, event) -> Publisher:       # a publisher per event
        return rabbit
```

`MemoryRepository(publish_on_commit=...)` takes the same. A channel per event is the queue's
`channel_of`, not another publisher.

- **What goes out by default:** every `DeliverableEventMixin` event the write recorded.
  `entities` narrows by the class name of the aggregate that recorded it; `when` is ANDed with
  the mark.
- **After the commit, never before.** Inside a unit of work the events wait for its
  `after_commit`, and a rollback drops them; outside one each `save` commits on its own, so they
  go out as `save` returns.
- **Taken once.** What goes out is pulled from the aggregate and the rest recorded back, so a
  later save or the Feature's own `pull_events()` never hands the same event twice. Do not also
  publish those events by hand.
- **A record of its own.** An event saved by itself (`save([DayClosed()])`) goes out when it was
  never tried.
- **The commit stands.** A publisher that raises never undoes it: the error is logged and stamped.

### Delivery metadata on the stored event

The stored `DeliverableEventMixin` row is stamped in a unit of work of its own:

| Field | Stored as | When |
|---|---|---|
| `delivered_at` | column | set when it went out; `created_at` is when it happened — both for audit |
| `delivery` | JSON | `{"attempts": n}` when it went out, plus `"last_error"` when it did not |

At once for a publisher that sends in the call; when the `Future` resolves for a forwarding one.
`delivered(at, attempts)`, `failed(error, attempts)` and `delivery_attempts` are the mixin's.
Neither field is in `as_json()`. What never went out is a criteria:
`Condition(field="delivered_at", operator=Operator.IS_NULL, value=True)` on the event class.

**The mark is a default filter only.** `DeliverableEventMixin` says "a contract other contexts
hear" (a versioned `name` is warned for) and is what `publish_on_commit` picks by default.
`publisher.publish(...)` never looks at it. On the explicit path — a Feature that pulls and
publishes — order, rollback and whether a failure matters are the Feature's call.

## Who guarantees what

| Stretch | Who answers for it |
|---|---|
| what to publish, and when | the Feature, or `publish_on_commit` |
| publisher → broker | the publisher: in the call, or its `Forwarding` |
| broker → consumer | the broker and the consumer's `subscribe` / `QueueGateway`: retries, dead letters, redelivery |
| the consumer storing it as its own event | the consumer's context |

Delivery to a consumer is at least once: a handler of an event is idempotent (the inbox,
`references/brokers.md`).

Framework evidence: `tests/ddd/repositories/test_publish_on_commit.py`,
`tests/event_driven/test_forwarding.py`, `tests/data_layer/orm/test_events_table.py`,
`docs/persistence/guide.md` section 12, `docs/events/README.md`.
