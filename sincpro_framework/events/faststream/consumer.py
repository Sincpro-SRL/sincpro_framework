"""`subscribe`: every event the buses registered, heard from a FastStream broker.

    subscribe(broker, Subscriber(planning, assurance))      # one subscription per event name
    await FastStream(broker).run()                           # or `faststream run app:app`

Context: nobody writes `@broker.subscriber("execution.v1.run_failed")` — the buses already say
which events they answer, so each gets its subscription here. A message is rebuilt as the
class the receiving context declared for that name, handed to the buses through their async
facade (a thread each, so the loop stays free) inside the trace that published it. A handler
that raises is the broker's to retry or dead-letter, as its configuration says.
"""

from collections.abc import Callable
from typing import Any

from faststream import Context, StreamMessage

from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.events.faststream.queue import EVENT_HEADER, Broker
from sincpro_framework.events.subscriber import Subscriber
from sincpro_framework.events.trace import within_trace

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


def _handler(subscriber: Subscriber, channel_name: str) -> Callable[..., Any]:
    async def handle(message: StreamMessage[Any] = Context("message")) -> None:
        name = message.headers.get(EVENT_HEADER, channel_name)
        event_type = subscriber.event_type(name)
        if event_type is None:
            return
        event = event_type.from_json(message.body.decode())
        with within_trace(message.headers):
            await subscriber.get_async_subscriber().handle(event)

    return handle


def subscribe(
    broker: Broker, subscriber: Subscriber, channel_of_name: ChannelOfName = by_name
) -> list[str]:
    """Subscribe the broker to the channel of every event the buses registered; answer the
    channels. Several events on one channel are one subscription, routed by the event header.
    """
    channels: dict[str, str] = {}
    for name in event_names(subscriber):
        channels.setdefault(channel_of_name(name), name)
    for channel, first_name in channels.items():
        broker.subscriber(channel)(_handler(subscriber, first_name))
    return list(channels)
