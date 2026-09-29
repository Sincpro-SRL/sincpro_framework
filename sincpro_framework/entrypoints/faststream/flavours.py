"""What differs from one broker to the next, said once: how a subscription is declared, whether the
broker counts deliveries, how a retry and a dead letter are carried, what a republished copy
keeps.

| Broker | Consumer group | Delivery count | Retry by default | Dead letter by default |
|---|---|---|---|---|
| Kafka | `group_id` | none | a copy with the attempt counted (a nack re-reads the offset and blocks the partition) | the `{channel}.dlq` topic |
| RabbitMQ | the queue (one queue per channel) | `x-delivery-count` (quorum queues) | nack | reject — the queue's dead-letter exchange |
| Redis | Streams group; Pub/Sub none | none | a copy with the attempt counted | the `{channel}.dlq` channel |
| NATS | queue group | JetStream's `num_delivered` | nack | the `{channel}.dlq` subject |

Context: the broker is recognised by its class's module, so no broker library is imported to
ask; a broker the table does not know gets the portable choices (a counted copy, a dead-letter
channel).
"""

from collections.abc import Callable
from enum import StrEnum
from typing import Any, Literal

from faststream import AckPolicy
from pydantic import ConfigDict

from sincpro_framework.sincpro_abstractions import DataTransferObject


class RetryBy(StrEnum):
    NACK = "nack"
    """The broker redelivers the message as it is — max_attempts holds only where the broker
    counts deliveries."""
    REPUBLISH = "republish"
    """A copy is published to the same channel with `sincpro-attempts` + 1, then the delivery
    is acked — counted on every broker; the copy goes to the back of the channel."""


class DeadLetterBy(StrEnum):
    CHANNEL = "channel"
    """A copy to `{channel}{suffix}` with the reason in its headers, then the delivery is
    acked."""
    NATIVE = "native"
    """Reject: the broker's own dead-lettering (RabbitMQ's dead-letter exchange) keeps it —
    the reason is logged, the broker adds its own headers (`x-death`)."""


type BrokerName = Literal["kafka", "rabbit", "redis", "nats", "other"]


class Flavour(DataTransferObject):
    model_config = ConfigDict(frozen=True)

    name: BrokerName
    retry_by: RetryBy
    dead_letter_by: DeadLetterBy
    acknowledges: bool = True
    """Redis Pub/Sub has no acknowledgement: at most once whatever is settled."""


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


def broker_name(broker: Any) -> BrokerName:
    module = type(broker).__module__
    for name in ("kafka", "rabbit", "redis", "nats"):
        if module.startswith(f"faststream.{name}"):
            return name  # type: ignore[return-value]
    return "other"


def flavour_of(broker: Any) -> Flavour:
    return FLAVOURS[broker_name(broker)]


def subscription_options(
    name: BrokerName, group: str, concurrency: int | None
) -> dict[str, Any]:
    """The keyword arguments of `broker.subscriber(channel, ...)` — the consumer group where the
    broker has one on a plain subscription, workers where it takes them, and MANUAL
    acknowledgement wherever there is one to make."""
    options: dict[str, Any] = {}
    workers = concurrency if concurrency is not None and concurrency > 1 else None
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
    """How many times the broker delivered this message — `None` where it does not say."""
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
