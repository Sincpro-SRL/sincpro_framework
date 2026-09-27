"""`FastStreamQueue` and `subscribe`: the same `publish(event)` and the same buses, over Kafka,
RabbitMQ, Redis or NATS — driven here by FastStream's in-memory test brokers.

Both flavours: `put` from synchronous code through a loop the queue owns, `aput` from a caller
that is already `async def`.
"""

import asyncio

import pytest
from faststream import Context, StreamMessage
from faststream.kafka import KafkaBroker, TestKafkaBroker
from faststream.rabbit import RabbitBroker, TestRabbitBroker

from sincpro_framework.ddd.exceptions import ContractViolation
from sincpro_framework.events import AsyncPublisher, Publisher, Subscriber
from sincpro_framework.events.faststream import FastStreamQueue, keyed_by_entity, subscribe

from .models import NobodyListens, TicketClosed, auditing_bus, notifying_bus


def _subscriber(heard: dict[str, list[str]]) -> Subscriber:
    return Subscriber(notifying_bus(heard["support"]), auditing_bus(heard["audit"]))


def test_an_event_published_from_sync_code_reaches_every_bus_that_registered_it(heard):
    broker = KafkaBroker()
    subscribe(broker, _subscriber(heard))

    async def scenario() -> None:
        async with TestKafkaBroker(broker):
            queue = FastStreamQueue(broker).start()
            await asyncio.to_thread(Publisher(queue).publish, TicketClosed(reason="sync"))
            queue.stop()

    asyncio.run(scenario())

    assert heard == {"support": ["sync"], "audit": ["audited sync"]}


def test_an_event_published_from_async_code_goes_through_the_callers_loop(heard):
    broker = RabbitBroker()
    subscribe(broker, _subscriber(heard))

    async def scenario() -> None:
        async with TestRabbitBroker(broker):
            await AsyncPublisher(FastStreamQueue(broker)).publish(
                TicketClosed(reason="async")
            )

    asyncio.run(scenario())

    assert heard == {"support": ["async"], "audit": ["audited async"]}


def test_sync_publishing_needs_the_loop_the_queue_owns(heard):
    queue = FastStreamQueue(KafkaBroker())

    with pytest.raises(ContractViolation, match="start()"):
        queue.put(TicketClosed(reason="nobody started it"))


def test_kafka_keeps_one_entitys_events_in_order_by_its_key():
    broker = KafkaBroker()
    keys: list[bytes] = []

    @broker.subscriber(TicketClosed.name)
    async def record(message: StreamMessage = Context("message")) -> None:
        keys.append(message.raw_message.key)

    async def scenario() -> None:
        async with TestKafkaBroker(broker):
            queue = FastStreamQueue(broker, options_of=keyed_by_entity)
            await queue.aput(TicketClosed(reason="x", entity_id="ticket-7"))

    asyncio.run(scenario())

    assert keys == [b"ticket-7"]


def test_several_events_can_share_one_channel_and_still_reach_their_buses(heard):
    broker = KafkaBroker()
    subscribe(broker, _subscriber(heard), channel_of_name=lambda _name: "tickets")

    async def scenario() -> None:
        async with TestKafkaBroker(broker):
            queue = FastStreamQueue(broker, channel_of=lambda _event: "tickets")
            await queue.aput(TicketClosed(reason="shared"))
            await queue.aput(NobodyListens())

    asyncio.run(scenario())

    assert heard == {"support": ["shared"], "audit": ["audited shared"]}


def test_only_the_events_the_buses_registered_get_a_subscription(heard):
    broker = KafkaBroker()

    channels = subscribe(broker, _subscriber(heard))

    assert channels == [TicketClosed.name]
