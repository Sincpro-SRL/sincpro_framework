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

from sincpro_framework import (
    DataTransferObject,
    Feature,
    ProgrammingError,
    ServiceUnavailableError,
)
from sincpro_framework.entrypoints.adapters.faststream import subscribe
from sincpro_framework.event_driven import (
    AsyncPublisher,
    Publisher,
    Subscriber,
)
from sincpro_framework.event_driven.adapters.faststream import (
    FastStreamQueue,
    keyed_by_entity,
)

from .models import NobodyListens, TicketClosed, auditing_bus, notifying_bus


class CommandOnQueue(DataTransferObject):
    pass


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


def test_a_broker_that_does_not_take_the_event_is_unavailable(heard):
    """The event was written and its channel chosen; the broker refusing it is a dependency
    down — retry later — with the driver's error as its cause, never a crash."""
    broker = KafkaBroker()

    async def refused(*args, **kwargs):
        raise ConnectionRefusedError(111, "Connection refused")

    broker.publish = refused  # type: ignore[method-assign]
    queue = FastStreamQueue(broker)

    async def scenario() -> None:
        await AsyncPublisher(queue).publish(TicketClosed(reason="down"))

    with pytest.raises(ServiceUnavailableError) as raised:
        asyncio.run(scenario())

    assert isinstance(raised.value.__cause__, ConnectionRefusedError)


def test_sync_publishing_needs_the_loop_the_queue_owns(heard):
    queue = FastStreamQueue(KafkaBroker())

    with pytest.raises(ProgrammingError, match="start()"):
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


def test_a_command_is_never_consumed_by_subscribe_whatever_it_declares(heard):
    """`subscribe` hears events. A Command on a queue is what an outsider makes this process do:
    it is `QueueGateway`'s, where who may send it is checked."""
    from sincpro_framework.entrypoints.entrypoint.decorators import queue

    bus = notifying_bus(heard["support"])

    @bus.feature(CommandOnQueue)
    @queue.consumes("support.commands")
    class OnQueue(Feature):
        def execute(self, dto: CommandOnQueue) -> None: ...

    assert subscribe(KafkaBroker(), Subscriber(bus)) == [TicketClosed.name]


def test_buses_hearing_one_event_share_one_subscription(heard):
    """Two subscriptions on one RabbitMQ queue compete: each bus would hear half the events."""
    broker = KafkaBroker()

    subscribe(broker, _subscriber(heard))

    assert len(broker.subscribers) == 1


def test_only_the_events_the_buses_registered_get_a_subscription(heard):
    broker = KafkaBroker()

    channels = subscribe(broker, _subscriber(heard))

    assert channels == [TicketClosed.name]


# --- How a received message is settled: at least once, and never a poison loop --------------

from faststream import AckPolicy, BaseMiddleware  # noqa: E402
from faststream.message import AckStatus  # noqa: E402

from sincpro_framework.entrypoints.adapters.faststream import QueueOptions  # noqa: E402

from .models import failing_bus  # noqa: E402


def _settling_spy(settled: list) -> type[BaseMiddleware]:
    """A broker middleware that keeps how each consumed message was left: acked, nacked
    (redelivered) or rejected (never redelivered)."""

    class Spy(BaseMiddleware):
        async def consume_scope(self, call_next, msg):  # type: ignore[no-untyped-def]
            try:
                return await call_next(msg)
            finally:
                settled.append(msg.committed)

    return Spy


BROKERS = [
    pytest.param(KafkaBroker, TestKafkaBroker, id="kafka"),
    pytest.param(RabbitBroker, TestRabbitBroker, id="rabbit"),
]


async def _deliver(broker, test_broker, body: bytes, headers: dict | None = None) -> None:  # type: ignore[no-untyped-def]
    async with test_broker(broker) as connected:
        try:
            await connected.publish(body, TicketClosed.name, headers=headers or {})
        except Exception:
            pass  # the test broker hands the handler's exception back to the publisher


def test_a_handler_that_raises_leaves_the_message_to_be_redelivered():
    """The at-most-once regression: FastStream's defaults commit a Kafka offset before the
    handler runs and discard a failed RabbitMQ message — the event was lost while the docs
    promised at least once. On RabbitMQ a failed handler hands the message back (nack)."""
    settled: list = []
    broker = RabbitBroker(middlewares=[_settling_spy(settled)])
    subscribe(broker, Subscriber(failing_bus()))

    asyncio.run(
        _deliver(broker, TestRabbitBroker, TicketClosed(reason="x").as_json().encode())
    )

    assert settled == [AckStatus.NACKED]


def test_on_kafka_a_handler_that_raises_is_retried_by_a_counted_copy_then_dead_lettered():
    """Kafka has no per-message redelivery: a nack re-reads the offset and blocks the partition
    behind one message. The retry is a copy with its attempt counted; at `max_attempts` the
    message goes to `{channel}.dlq` — never lost, never a poison loop."""
    dead: list[dict] = []
    broker = KafkaBroker()
    subscribe(broker, Subscriber(failing_bus()), options=QueueOptions(max_attempts=3))

    @broker.subscriber(f"{TicketClosed.name}.dlq")
    async def dead_letters(message: StreamMessage = Context("message")) -> None:  # type: ignore[type-arg]
        dead.append(dict(message.headers))

    asyncio.run(
        _deliver(broker, TestKafkaBroker, TicketClosed(reason="x").as_json().encode())
    )

    assert len(dead) == 1
    assert dead[0]["sincpro-attempts"] == "3"


@pytest.mark.parametrize(("broker_cls", "test_broker"), BROKERS)
def test_a_handled_message_is_acknowledged_after_its_buses_ran(
    broker_cls, test_broker, heard
):
    settled: list = []
    broker = broker_cls(middlewares=[_settling_spy(settled)])
    subscribe(broker, _subscriber(heard))

    asyncio.run(_deliver(broker, test_broker, TicketClosed(reason="ok").as_json().encode()))

    assert heard["support"] == ["ok"]
    assert settled == [AckStatus.ACKED]


@pytest.mark.parametrize(
    ("broker_cls", "test_broker", "never_redelivered"),
    [
        # Kafka has no reject: FastStream commits the offset, which skips the message.
        pytest.param(KafkaBroker, TestKafkaBroker, AckStatus.ACKED, id="kafka"),
        pytest.param(RabbitBroker, TestRabbitBroker, AckStatus.REJECTED, id="rabbit"),
    ],
)
def test_a_message_that_cannot_be_rebuilt_is_never_redelivered(
    broker_cls, test_broker, never_redelivered, heard
):
    """A payload of another shape will fail on every delivery: redelivering it would block a
    Kafka partition or spin a queue forever. It is rejected — RabbitMQ dead-letters it when
    the queue has a dead-letter exchange; Kafka skips it — and logged, so it is not lost
    silently."""
    settled: list = []
    broker = broker_cls(middlewares=[_settling_spy(settled)])
    subscribe(broker, _subscriber(heard))

    asyncio.run(_deliver(broker, test_broker, b"not an event"))

    assert settled == [never_redelivered]
    assert settled != [AckStatus.NACKED]
    assert heard == {"support": [], "audit": []}


def test_an_event_no_bus_here_registered_is_acknowledged_as_anothers(heard):
    """On a channel several events share, one nobody here answers is another consumer's:
    acknowledged, never redelivered to this one."""
    settled: list = []
    broker = KafkaBroker(middlewares=[_settling_spy(settled)])
    subscribe(broker, _subscriber(heard), channel_of_name=lambda _name: "tickets")

    async def scenario() -> None:
        async with TestKafkaBroker(broker):
            await FastStreamQueue(broker, channel_of=lambda _event: "tickets").aput(
                NobodyListens()
            )

    asyncio.run(scenario())

    assert settled == [AckStatus.ACKED]


def test_every_subscription_settles_manually_whatever_the_brokers_default():
    """The settlement is the framework's, never FastStream's per-broker default."""
    broker = KafkaBroker()
    subscribe(broker, Subscriber(failing_bus()))

    (subscription,) = broker.subscribers
    assert subscription.ack_policy is AckPolicy.MANUAL
