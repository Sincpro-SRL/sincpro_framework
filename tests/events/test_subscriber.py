"""A subscriber is the buses it was given, and executes the ones whose registry knows the
event — a Feature here, an ApplicationService there, nothing on the bus that said nothing."""

import asyncio

from sincpro_framework.events import Subscriber

from .models import (
    CommandAudit,
    NobodyListens,
    ResponseAudit,
    ResponseNotify,
    Ticket,
    TicketClosed,
)


def test_an_entity_records_and_the_feature_pulls():
    ticket = Ticket(title="printer")
    ticket.close("fixed")

    recorded = ticket.pull_events()

    assert [one.name for one in recorded] == ["TicketClosed"]
    assert recorded[0].entity_id == ticket.id and recorded[0].entity_type == "Ticket"
    assert ticket.pull_events() == [], "pulled once, gone: nothing here stores an event"


def test_several_framework_instances_each_obey_the_event_their_decorator_names(
    subscriber, feature_bus, app_service_bus, heard
):
    """Three `UseFramework` instances. The event reaches the Feature and the ApplicationService
    that named it in their decorator, in the order the buses were given, and the silent one
    hears nothing. The command reaches only the bus that registered it."""
    answers = subscriber.handle(TicketClosed(reason="fixed"))

    assert answers == [ResponseNotify(sent="fixed"), ResponseAudit(logged="audited fixed")]
    assert heard == {"support": ["fixed"], "audit": ["audited fixed"]}
    assert subscriber.listeners("TicketClosed") == [feature_bus, app_service_bus]
    assert subscriber.listeners(f"{CommandAudit.__module__}.{CommandAudit.__qualname__}") == [
        app_service_bus
    ]
    assert subscriber.listeners("NobodyListens") == []
    assert (
        app_service_bus(CommandAudit(text="by command"), ResponseAudit).logged == "by command"
    )


def test_an_event_nobody_knows_answers_nothing(subscriber):
    assert subscriber.handle(NobodyListens()) == []
    assert subscriber.event_type("TicketClosed") is TicketClosed
    assert subscriber.event_type("NobodyListens") is None


def test_the_async_subscriber_runs_each_bus_through_its_async_facade(subscriber, heard):
    answers = asyncio.run(
        subscriber.get_async_subscriber().handle(TicketClosed(reason="direct"))
    )

    assert answers == [ResponseNotify(sent="direct"), ResponseAudit(logged="audited direct")]
    assert heard == {"support": ["direct"], "audit": ["audited direct"]}


def test_a_subscriber_may_be_empty():
    empty = Subscriber()

    assert empty.handle(TicketClosed(reason="x")) == []
    assert empty.listeners("TicketClosed") == []
