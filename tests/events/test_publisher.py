"""A publisher emits with the signature of a bus, in a sync or an async caller, and only
promises an answer where one bus can give it in the same call."""

import asyncio

import pytest

from sincpro_framework.ddd.exceptions import ContractViolation
from sincpro_framework.events import Publisher, Subscriber, SyncQueue

from .models import (
    CommandAudit,
    CommandNotify,
    NobodyListens,
    ResponseAudit,
    ResponseNotify,
    TicketClosed,
)


def test_publish_launches_and_forgets(publisher, heard):
    assert publisher.publish(TicketClosed(reason="fixed")) is None
    assert publisher.publish(NobodyListens()) is None

    assert heard == {"support": ["fixed"], "audit": ["audited fixed"]}


def test_one_event_reaches_a_feature_and_an_app_service_on_different_buses(
    publisher, feature_bus, app_service_bus, heard
):
    """The real shape: two `UseFramework` instances, a Feature on one and an ApplicationService
    on the other, both registered with `[Command, TicketClosed]`. One `publish` and both run;
    each command still reaches only its own bus."""
    publisher.publish(TicketClosed(reason="shared"))

    assert feature_bus(CommandNotify(text="mine"), ResponseNotify).sent == "mine"
    assert app_service_bus(CommandAudit(text="theirs"), ResponseAudit).logged == "theirs"
    assert heard == {"support": ["shared", "mine"], "audit": ["audited shared", "theirs"]}


def test_publish_with_a_type_waits_for_the_answer_like_a_command(feature_bus, heard):
    publisher = Publisher(SyncQueue(Subscriber(feature_bus)))

    answer = publisher.publish(TicketClosed(reason="fixed"), ResponseNotify)

    assert answer == ResponseNotify(sent="fixed")
    assert heard["support"] == ["fixed"]


def test_a_typed_publish_needs_exactly_one_bus_that_answers(publisher):
    with pytest.raises(ContractViolation, match="2 subscribers"):
        publisher.publish(TicketClosed(reason="x"), ResponseNotify)
    with pytest.raises(ContractViolation, match="0 subscribers"):
        publisher.publish(NobodyListens(), ResponseNotify)


def test_what_a_bus_raises_the_publisher_raises(broken_bus):
    publisher = Publisher(SyncQueue(Subscriber(broken_bus)))

    with pytest.raises(RuntimeError, match="down"):
        publisher.publish(TicketClosed(reason="x"))


def test_the_async_publisher_follows_the_bus(feature_bus, heard):
    """The same two forms, awaited: typed when one bus answers, `None` when launching."""
    publisher = Publisher(SyncQueue(Subscriber(feature_bus))).get_async_publisher()

    async def scenario() -> tuple[ResponseNotify, None]:
        typed = await publisher.publish(TicketClosed(reason="awaited"), ResponseNotify)
        forgotten = await publisher.publish(NobodyListens())
        return typed, forgotten

    typed, forgotten = asyncio.run(scenario())

    assert typed == ResponseNotify(sent="awaited") and forgotten is None
    assert heard["support"] == ["awaited"]


def test_the_async_publisher_fans_out_concurrently(publisher, heard):
    """What an `async def` caller does with it: many publishes in one `gather`."""

    async def scenario() -> None:
        awaited = publisher.get_async_publisher()
        await asyncio.gather(
            *(awaited.publish(TicketClosed(reason=f"t{n}")) for n in range(5))
        )

    asyncio.run(scenario())

    assert sorted(heard["support"]) == [f"t{n}" for n in range(5)]
    assert sorted(heard["audit"]) == [f"audited t{n}" for n in range(5)]


def test_a_command_is_refused_because_a_queue_carries_facts(sync_queue):
    """A command is something one bus is asked to do, and it is asked directly. Published, it
    used to fail deep inside the queue with `'CommandAudit' object has no attribute 'name'` —
    true, and no help at all about why."""
    with pytest.raises(ContractViolation, match="facts rather than orders"):
        Publisher(sync_queue).publish(CommandAudit(text="do this"))  # type: ignore[arg-type]
