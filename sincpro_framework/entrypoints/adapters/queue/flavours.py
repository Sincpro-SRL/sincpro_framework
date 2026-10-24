"""What differs from one broker to the next, said once: how a subscription is declared, whether the
broker counts deliveries, how a retry and a dead letter are carried, what a republished copy
keeps.

| Broker | Consumer group | Delivery count | Retry by default | Dead letter by default |
|---|---|---|---|---|
| Kafka | `group_id` | none | a copy with the attempt counted (a nack re-reads the offset and blocks the partition) | the `{channel}.dlq` topic |
| RabbitMQ | the queue (one queue per channel) | `x-delivery-count` (quorum queues) | nack | reject — the queue's dead-letter exchange |
| Redis | Streams group; Pub/Sub none | none | a copy with the attempt counted | the `{channel}.dlq` channel |
| NATS | queue group | JetStream's `num_delivered` | nack | the `{channel}.dlq` subject |
| Amazon SQS | none: every consumer competes | `ApproximateReceiveCount` | nack — the message is visible again | reject — the queue's redrive policy |

Context: a FastStream broker is recognised by its class's module, so no broker library is imported
to ask; a broker of another kind says its own with a `flavour` attribute (the SQS broker does); a
broker neither knows gets the portable choices (a counted copy, a dead-letter channel).
"""

from collections.abc import Callable
from enum import StrEnum
from typing import Any, Literal

from pydantic import ConfigDict

from sincpro_framework.sincpro_abstractions import DataTransferObject


class RetryBy(StrEnum):
    """How a delivery the verdict retries is carried back — `QueueOptions.retry_by` overrides
    the broker's default."""

    NACK = "nack"
    """The broker redelivers the message as it is — max_attempts holds only where the broker
    counts deliveries."""
    REPUBLISH = "republish"
    """A copy is published to the same channel with `sincpro-attempts` + 1, then the delivery
    is acked — counted on every broker; the copy goes to the back of the channel."""


class DeadLetterBy(StrEnum):
    """Where a dead-lettered delivery goes — `QueueOptions.dead_letter_by` overrides the
    broker's default."""

    CHANNEL = "channel"
    """A copy to `{channel}{suffix}` with the reason in its headers, then the delivery is
    acked."""
    NATIVE = "native"
    """Reject: the broker's own dead-lettering (RabbitMQ's dead-letter exchange) keeps it —
    the reason is logged, the broker adds its own headers (`x-death`)."""


type BrokerName = Literal["kafka", "rabbit", "redis", "nats", "sqs", "other"]


class Flavour(DataTransferObject):
    """What one kind of broker is like, for the wire to settle on it — said once per broker.

        Flavour(name="kafka", retry_by=RetryBy.REPUBLISH, dead_letter_by=DeadLetterBy.CHANNEL)
        Flavour(name="sqs", retry_by=RetryBy.NACK, dead_letter_by=DeadLetterBy.NATIVE,
                faststream=False)                     # what `SqsBroker.flavour` says

    Context: a broker that is not FastStream's declares its own as a `flavour` attribute;
    `flavour_of` finds it.
    """

    model_config = ConfigDict(frozen=True)

    name: BrokerName
    retry_by: RetryBy
    dead_letter_by: DeadLetterBy
    acknowledges: bool = True
    """Redis Pub/Sub has no acknowledgement: at most once whatever is settled."""
    faststream: bool = True
    """A FastStream broker: subscribed with its manual acknowledgement policy, and handing the
    message by its `Context`. A broker of another kind says `False` in its own flavour."""


FLAVOURS: dict[BrokerName, Flavour] = {
    "kafka": Flavour(
        name="kafka", retry_by=RetryBy.REPUBLISH, dead_letter_by=DeadLetterBy.CHANNEL
    ),
    "rabbit": Flavour(
        name="rabbit", retry_by=RetryBy.NACK, dead_letter_by=DeadLetterBy.NATIVE
    ),
    "redis": Flavour(
        name="redis",
        retry_by=RetryBy.REPUBLISH,
        dead_letter_by=DeadLetterBy.CHANNEL,
        acknowledges=False,
    ),
    "nats": Flavour(name="nats", retry_by=RetryBy.NACK, dead_letter_by=DeadLetterBy.CHANNEL),
    "other": Flavour(
        name="other", retry_by=RetryBy.REPUBLISH, dead_letter_by=DeadLetterBy.CHANNEL
    ),
}


def flavour_of(broker: Any) -> Flavour:
    """The broker's own `flavour` when it says one, else the FastStream broker its class's module
    names, else the portable choices.

        flavour_of(KafkaBroker("localhost:9092"))   ->  FLAVOURS["kafka"]
        flavour_of(SqsBroker())                     ->  its own `flavour`
        flavour_of(object())                        ->  FLAVOURS["other"]

    The module is matched by prefix, so `faststream.rabbit.broker` is rabbit.
    """
    declared = getattr(broker, "flavour", None)
    if isinstance(declared, Flavour):
        return declared
    module = type(broker).__module__
    for name in ("kafka", "rabbit", "redis", "nats"):
        if module.startswith(f"faststream.{name}"):
            return FLAVOURS[name]  # type: ignore[index]
    return FLAVOURS["other"]


def subscription_options(
    flavour: Flavour, group: str, concurrency: int | None
) -> dict[str, Any]:
    """The keyword arguments of `broker.subscriber(channel, ...)` — the consumer group where the
    broker has one on a plain subscription, workers where it takes them, and MANUAL
    acknowledgement wherever there is one to make. A broker that is not FastStream's is given
    its workers only.

        subscription_options(FLAVOURS["kafka"], "billing", 4)
        ->  {"group_id": "billing", "max_workers": 4, "ack_policy": AckPolicy.MANUAL}
        subscription_options(FLAVOURS["redis"], "billing", None)     ->  {}
        subscription_options(<SqsBroker's flavour>, "billing", 1)    ->  {}

    1. Workers only above one — one is what every subscriber has.
    2. Not FastStream's: the workers alone, if any.
    3. Per broker:
       3.1 kafka — the group as `group_id`, and the workers;
       3.2 redis — the workers, and nothing else: Pub/Sub has no acknowledgement;
       3.3 nats — the group as `queue`, and the workers;
       3.4 rabbit and any other — neither group nor workers.
    Final: MANUAL acknowledgement, for every FastStream broker but redis.
    """
    options: dict[str, Any] = {}
    workers = concurrency if concurrency is not None and concurrency > 1 else None
    name = flavour.name
    if not flavour.faststream:
        return {"max_workers": workers} if workers else options
    from faststream import AckPolicy

    if name == "kafka":
        options["group_id"] = group
        if workers:
            options["max_workers"] = workers
    elif name == "redis":
        if workers:
            options["max_workers"] = workers
        return options  # Pub/Sub refuses an acknowledgement policy: it has none
    elif name == "nats":
        options["queue"] = group
        if workers:
            options["max_workers"] = workers
    options["ack_policy"] = AckPolicy.MANUAL
    return options


def delivery_count(name: BrokerName, message: Any) -> int | None:
    """How many times the broker delivered this message — `None` where it does not say. A
    delivery of a broker that is not FastStream's may say it itself, as `delivery_count`.

        delivery_count("rabbit", <headers {"x-delivery-count": 2}>)   ->  3
        delivery_count("nats", <metadata.num_delivered == 2>)         ->  2
        delivery_count("kafka", <message>)                            ->  None
        delivery_count("other", <SqsDelivery, delivery_count=4>)      ->  4

    1. The message's own `delivery_count`, when it is an integer — a `bool` is not one.
    2. rabbit: a quorum queue's `x-delivery-count` header counts the redeliveries before
       this one — plus one; absent or unreadable, `None`.
    3. nats: the JetStream metadata's `num_delivered`, when there is one.
    Final: `None` — kafka and redis do not count.
    """
    counted = getattr(message, "delivery_count", None)
    if isinstance(counted, int) and not isinstance(counted, bool):
        return counted
    if name == "rabbit":
        count = (message.headers or {}).get("x-delivery-count")
        try:
            # a quorum queue counts the redeliveries before this one
            return None if count is None else int(count) + 1
        except (TypeError, ValueError):
            return None
    if name == "nats":
        metadata = getattr(message.raw_message, "metadata", None)
        delivered = getattr(getattr(metadata, "num_delivered", None), "__int__", None)
        return None if delivered is None else int(delivered())
    return None


def republish_options(name: BrokerName, message: Any) -> dict[str, Any]:
    """What a copy keeps of the original so it lands where the original did — Kafka's key,
    so one entity's messages share a partition."""
    if name == "kafka":
        key = getattr(message.raw_message, "key", None)
        return {"key": key} if key is not None else {}
    return {}


type SubscriptionOf = Callable[[Any, str, str, int | None], Callable[..., Any]]
"""`(broker, channel, group, concurrency) -> broker.subscriber(...)` — how a project declares a
subscription the defaults do not cover: a RabbitMQ exchange per consumer group, a Redis Stream,
a JetStream stream. It MUST settle manually (`ack_policy=AckPolicy.MANUAL`)."""
