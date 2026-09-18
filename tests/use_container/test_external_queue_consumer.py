"""The recipe an external broker consumer (Kafka, RabbitMQ) follows: a message arrives as
raw (name, payload) — no class, no Python object, just what crossed the wire — and the
consumer has to find who answers it, rebuild it, and run it. Every piece here is public:
`Subscriber` to find the buses, `map_to_dto_or_event` to rebuild, `bus(...)` to run.
"""

from dataclasses import dataclass

from sincpro_framework import Feature, UseFramework
from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.events import Subscriber


@dataclass(kw_only=True)
class OrderShipped(DomainEvent):
    order_id: str


def _billing_bus(heard: list) -> UseFramework:
    bus = UseFramework("billing", log_after_execution=False)
    bus.add_dependency("heard", heard)

    @bus.feature(OrderShipped)
    class Invoice(Feature):
        heard: list

        def execute(self, dto: OrderShipped) -> None:
            self.heard.append(("billing", dto.order_id))

    return bus


def _shipping_bus(heard: list) -> UseFramework:
    bus = UseFramework("shipping", log_after_execution=False)
    bus.add_dependency("heard", heard)

    @bus.feature(OrderShipped)
    class NotifyCarrier(Feature):
        heard: list

        def execute(self, dto: OrderShipped) -> None:
            self.heard.append(("shipping", dto.order_id))

    return bus


def test_a_raw_wire_message_reaches_every_bus_that_answers_it():
    heard: list = []
    billing = _billing_bus(heard)
    shipping = _shipping_bus(heard)
    subscriber = Subscriber(billing, shipping)

    # What actually crosses a broker: a name and a JSON string, nothing else.
    wire_name, wire_payload = "OrderShipped", '{"order_id": "ORD-1"}'

    for bus in subscriber.listeners(wire_name):
        event = bus.map_to_dto_or_event(wire_name, wire_payload)
        bus(event)

    assert sorted(heard) == [("billing", "ORD-1"), ("shipping", "ORD-1")]


def test_a_raw_message_for_an_unregistered_name_reaches_no_bus():
    heard: list = []
    subscriber = Subscriber(_billing_bus(heard), _shipping_bus(heard))

    assert subscriber.listeners("SomethingElseHappened") == []
