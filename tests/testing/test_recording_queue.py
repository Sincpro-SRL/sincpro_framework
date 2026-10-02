"""`RecordingQueue`: what a use case published, for a test to assert on — the way MassTransit's
test harness answers `Published` and Axon's fixture `expectEvents`."""

import asyncio
from dataclasses import dataclass

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.event_driven import (
    Publisher,
    Subscriber,
    SyncQueue,
)
from sincpro_framework.testing import RecordingQueue


@dataclass(kw_only=True)
class InvoiceIssued(DomainEvent):
    name = "billing.v1.invoice_issued_recorded"
    invoice_id: str = ""


@dataclass(kw_only=True)
class InvoiceVoided(DomainEvent):
    name = "billing.v1.invoice_voided_recorded"
    invoice_id: str = ""


class CommandIssue(DataTransferObject):
    invoice_id: str


def _billing(publisher: Publisher) -> UseFramework:
    billing = UseFramework("billing-recorded", log_after_execution=False)
    billing.add_dependency("publisher", publisher)

    @billing.feature(CommandIssue)
    class Issue(Feature):
        publisher: Publisher

        def execute(self, dto: CommandIssue) -> None:
            self.publisher.publish(InvoiceIssued(invoice_id=dto.invoice_id))
            self.publisher.publish(InvoiceVoided(invoice_id="old"))

    return billing


def test_it_keeps_every_event_published_in_order():
    published = RecordingQueue()

    _billing(Publisher(published))(CommandIssue(invoice_id="F-1"))

    assert [type(one) for one in published.events] == [InvoiceIssued, InvoiceVoided]
    assert [one.invoice_id for one in published.of(InvoiceIssued)] == ["F-1"]


def test_it_hands_each_event_on_to_the_queue_it_wraps():
    heard: list[str] = []
    accounting = UseFramework("accounting-recorded", log_after_execution=False)

    @accounting.feature(InvoiceIssued)
    class Book(Feature):
        def execute(self, dto: InvoiceIssued) -> None:
            heard.append(dto.invoice_id)

    published = RecordingQueue(SyncQueue(Subscriber(accounting)))

    _billing(Publisher(published))(CommandIssue(invoice_id="F-1"))

    assert heard == ["F-1"] and len(published.events) == 2


def test_it_records_what_is_published_from_async_code_and_forgets_on_clear():
    published = RecordingQueue()

    asyncio.run(
        Publisher(published).get_async_publisher().publish(InvoiceIssued(invoice_id="F-2"))
    )
    assert [one.invoice_id for one in published.of(InvoiceIssued)] == ["F-2"]

    published.clear()
    assert published.events == []
