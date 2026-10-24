"""`RecordingQueue`: the events a use case published, kept for a test to assert on.

    published = RecordingQueue()                                    # nothing hears them
    published = RecordingQueue(SyncQueue(Subscriber(accounting)))   # and the subscribers still do
    billing.add_dependency("publisher", Publisher(published))
    billing(CommandIssue(invoice_id="F-1"))
    assert [one.invoice_id for one in published.of(InvoiceIssued)] == ["F-1"]

Context: it is a `Queue` like any other, so the production wiring stays as it is and only the
queue behind the `Publisher` changes — MassTransit's test harness and Axon's `expectEvents`,
without a harness to learn.
"""

from typing import Any

from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.event_driven.domain.queue import Queue


class RecordingQueue:
    """A `Queue` that keeps every event put on it, in order, and hands it on to `then`."""

    def __init__(self, then: Queue | None = None) -> None:
        """`then` is the queue each event is handed on to after it is kept; none, and nobody
        hears it."""
        self.then = then
        self.events: list[DomainEvent] = []

    def put(self, event: DomainEvent) -> list[Any] | None:
        """Keep `event`, then hand it to `then` — `None` when there is none, else what `then`
        answers."""
        self.events.append(event)
        return None if self.then is None else self.then.put(event)

    async def aput(self, event: DomainEvent) -> list[Any] | None:
        """`put` for an async publisher — `then` is awaited through its own `aput`."""
        self.events.append(event)
        return None if self.then is None else await self.then.aput(event)

    def of[E: DomainEvent](self, event_type: type[E]) -> list[E]:
        """The events of `event_type`, a subclass included, in the order they were published."""
        return [event for event in self.events if isinstance(event, event_type)]

    def clear(self) -> None:
        """Forget what was kept — between two acts of one test."""
        self.events.clear()
