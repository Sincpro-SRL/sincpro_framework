"""What the framework's own pieces count, declaring nothing (PRD_03 §4.2): each cache answer,
each idempotent run, each queue delivery and how it was settled.

The incidents: a fail-safe serving stale answers through an outage nobody sees, duplicates
replayed at a rate nobody knows, dead letters piling up with no alert to fire.
"""

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta

import pytest

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.caching import Cache, Idempotency, InMemoryKeyValue
from sincpro_framework.ddd import DomainEvent
from sincpro_framework.observability.metrics import InMemoryRecorder, metrics


@pytest.fixture
def recorder() -> Iterator[InMemoryRecorder]:
    recorded = InMemoryRecorder()
    with metrics.using(recorded):
        yield recorded


def test_every_cache_answer_is_counted_by_namespace_and_outcome(recorder):
    tenants = Cache(namespace="tenants")

    tenants.get_or_compute("acme", lambda: 1)
    tenants.get_or_compute("acme", lambda: 2)

    assert recorder.totals("sincpro.cache.outcomes") == {
        (("sincpro.namespace", "tenants"), ("sincpro.outcome", "computed")): 1,
        (("sincpro.namespace", "tenants"), ("sincpro.outcome", "hit")): 1,
    }


class CommandCharge(DataTransferObject):
    amount: int

    def idempotency_key(self) -> str:
        return f"charge-{self.amount}"


class ResponseCharge(DataTransferObject):
    receipt: int


def test_every_idempotent_run_is_counted_by_outcome(recorder):
    idempotency = Idempotency(InMemoryKeyValue(), namespace="payments")
    bus = UseFramework("payments", log_after_execution=False)

    @bus.feature(CommandCharge)
    @idempotency.once(expires_after=timedelta(minutes=1))
    class Charge(Feature):
        def execute(self, dto: CommandCharge) -> ResponseCharge:
            return ResponseCharge(receipt=dto.amount)

    bus(CommandCharge(amount=5), ResponseCharge)
    bus(CommandCharge(amount=5), ResponseCharge)

    counted = {
        dict(labels)["sincpro.outcome"]: value
        for labels, value in recorder.totals("sincpro.idempotency.outcomes").items()
    }
    assert counted == {"claimed": 1, "replayed": 1}


@dataclass(kw_only=True)
class TicketClosed(DomainEvent):
    name = "support.ticket.v1.closed"
    reason: str = ""


def test_every_queue_delivery_is_counted_by_how_it_was_settled(recorder):
    from faststream.kafka import KafkaBroker, TestKafkaBroker

    from sincpro_framework.entrypoints.faststream import QueueGateway, QueueOptions

    bus = UseFramework("support", log_after_execution=False)

    @bus.feature(TicketClosed)
    class Close(Feature):
        def execute(self, dto: TicketClosed) -> None:
            if dto.reason == "boom":
                raise RuntimeError("down")

    broker = KafkaBroker()
    QueueGateway(broker, [bus], options=QueueOptions(max_attempts=2)).build()

    async def scenario() -> None:
        async with TestKafkaBroker(broker) as connected:
            await connected.publish(
                TicketClosed(reason="ok").as_json().encode(), TicketClosed.name
            )
            await connected.publish(
                TicketClosed(reason="boom").as_json().encode(), TicketClosed.name
            )

    asyncio.run(scenario())

    settled = {
        dict(labels)["sincpro.settlement"]: value
        for labels, value in recorder.totals("sincpro.queue.deliveries").items()
    }
    assert settled == {"ack": 1, "retry": 1, "dead_letter": 1}
    assert all(
        dict(labels)["messaging.destination.name"] == TicketClosed.name
        for labels in recorder.totals("sincpro.queue.deliveries")
    )
