"""A subscriber is the buses it was given, and executes the ones whose registry knows the
event — a Feature here, an ApplicationService there, nothing on the bus that said nothing."""

import asyncio
from dataclasses import dataclass

from sincpro_framework import Feature, UseFramework
from sincpro_framework.ddd.events import DomainEvent
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


# --- the two identities of an event, and which one travels ---------------------------------


def _declaring(wire: str, suffix: str) -> type:
    """A context's own class for a wire name — what a context that may not import the
    publisher's module writes for itself."""

    @dataclass(kw_only=True)
    class Restated(DomainEvent):
        name = wire
        reason: str = ""

    Restated.__name__ = f"Restated_{suffix}"
    return Restated


def _listening(name: str, declared: type, into: list[str]) -> UseFramework:
    bus = UseFramework(name, log_after_execution=False)

    @bus.feature(declared)
    class Reacts(Feature):
        def execute(self, dto) -> ResponseNotify:
            into.append(f"{name}:{type(dto).__name__}")
            return ResponseNotify(sent="ok")

    return bus


def test_each_bus_is_handed_the_class_it_declared_for_that_wire_name():
    """An event has two identities and only one of them travels: the wire name crosses a
    process and a bounded context, the Python class cannot. A context that may not import the
    publisher's module declares its own class under the same name.

    The bus is chosen by name and then dispatches by class, so handing it the publisher's
    instance used to ask it for a class it never registered — `UnknownDTOToExecute`, and in a
    background worker that refusal is one log line in another process.
    """
    wire = "execution.v1.run_advanced"
    seen: list[str] = []
    first = _listening("catalog", _declaring(wire, "catalog"), seen)
    second = _listening("project", _declaring(wire, "project"), seen)

    published = _declaring(wire, "execution")(reason="fitted")
    answers = Subscriber(first, second).handle(published)

    assert seen == ["catalog:Restated_catalog", "project:Restated_project"]
    assert len(answers) == 2


def test_a_class_the_bus_itself_declared_is_passed_through_untouched():
    """Every event that never left its own context, and every project that shares one class
    across buses — nothing is rebuilt and the instance is the one published."""
    wire = "execution.v1.run_advanced"
    shared = _declaring(wire, "shared")
    seen: list[str] = []
    bus = _listening("catalog", shared, seen)

    published = shared(reason="fitted")
    assert Subscriber(bus).as_known_by(bus, published) is published


def test_a_bus_that_does_not_know_the_name_leaves_the_event_alone():
    wire = "execution.v1.run_advanced"
    stranger = UseFramework("stranger", log_after_execution=False)
    published = _declaring(wire, "execution")(reason="fitted")

    assert Subscriber(stranger).as_known_by(stranger, published) is published
    assert Subscriber(stranger).handle(published) == []
