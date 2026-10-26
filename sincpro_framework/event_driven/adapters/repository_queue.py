"""`RepositoryQueue`: `publish(event)` saves the event through the repository, in the unit of
work in play, instead of sending it — the relay sends it once the transaction committed.

    publisher = Publisher(RepositoryQueue(repository))
    with repository.context():
        repository.save(invoice)
        publisher.publish(InvoicePaid(invoice_id=invoice.id))    committed with the invoice

The Feature has one verb; whether `publish` sends now or saves the event for the relay is
the wiring's choice.
"""

from typing import Any

from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.ddd.repositories import IRepository


class RepositoryQueue:
    """A `Queue` saving events through a repository — an outbox — instead of sending them."""

    def __init__(self, repository: IRepository) -> None:
        """Save events through `repository` — the one the Feature saves its aggregate with,
        so both share the unit of work."""
        self.repository = repository

    def put(self, event: DomainEvent) -> list[Any] | None:
        """Save `event` in the unit of work in play; nobody answers yet, so `None`."""
        self.repository.save([event])
        return None

    async def aput(self, event: DomainEvent) -> list[Any] | None:
        """`put`, on the event loop — saving joins the unit of work, it sends nothing."""
        return self.put(event)
