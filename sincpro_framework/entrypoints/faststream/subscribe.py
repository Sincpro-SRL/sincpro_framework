"""`subscribe`: every event the buses registered, heard from a FastStream broker.

    from sincpro_framework.entrypoints.faststream import subscribe

    subscribe(broker, Subscriber(planning, assurance))      # one subscription per channel
    await FastStream(broker).run()                           # or `faststream run app:app`

Context: nobody writes `@broker.subscriber("execution.v1.run_failed")` — the buses already say
which events they answer. This is the shortcut of the queue entrypoint for that case, and only
that case: `QueueGateway` over the subscriber's buses, their events only, all of them in one
consumer group, so each channel is one subscription that hands the event to every bus that
registered it — as the class *that* bus declared for the name. Commands are never consumed from
here, whatever they declare: that is `QueueGateway`'s, where who may send one is checked.

How a message is settled is the queue entrypoint's (PRD_15 §5.2), never FastStream's per-broker
default — that default commits a Kafka offset before the handler runs and discards a failed
RabbitMQ message, which is at most once:

| What happened | Settled |
|---|---|
| every bus handled the event | ack |
| a redelivery the inbox saw completed | ack, no bus run again |
| a bus raised | retried — a nack, or on Kafka a counted copy — then dead-lettered at `max_attempts`; a retry re-runs only the bus that failed |
| the payload is not the event it claims to be | dead-lettered at once: RabbitMQ's own dead-letter exchange, `{channel}.dlq` elsewhere |
| no bus here registered that event (a shared channel) | ack — it is another consumer's |

Redis Pub/Sub and core NATS have no acknowledgement at all: on them delivery stays at most once
whatever is settled here — use Redis Streams or NATS JetStream when a lost event matters.
"""

from collections.abc import Callable
from typing import Any

from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.entrypoints.faststream.gateway import events_gateway
from sincpro_framework.event_driven import Subscriber
from sincpro_framework.event_driven.adapters.faststream import Broker

type ChannelOfName = Callable[[str], str]


def by_name(name: str) -> str:
    return name


def event_names(subscriber: Subscriber) -> list[str]:
    """The wire names of every event some bus of the subscriber registered, in order."""
    names: dict[str, None] = {}
    for bus in subscriber.buses:
        for name, registered in bus.dto_registry.items():
            if isinstance(registered, type) and issubclass(registered, DomainEvent):
                names[name] = None
    return list(names)


def subscribe(
    broker: Broker,
    subscriber: Subscriber,
    channel_of_name: ChannelOfName = by_name,
    options: Any | None = None,
) -> list[str]:
    """Subscribe the broker to the channel of every event the buses registered; answer the
    channels. Several events on one channel are one subscription, routed by the event's type.
    `options` is a `QueueOptions` — the inbox, `max_attempts`, the dead-letter suffix."""
    subscriptions = events_gateway(broker, subscriber.buses, channel_of_name, options).build()
    return list(dict.fromkeys(one.channel for one in subscriptions))
