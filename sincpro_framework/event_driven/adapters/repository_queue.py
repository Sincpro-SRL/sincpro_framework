"""`RepositoryQueue`: `publish(event)` keeps the event through the repository, in the unit of
work in play, instead of sending it — the relay sends it once the transaction committed.

    publisher = Publisher(RepositoryQueue(repository))
    with repository.context():
        repository.save(invoice)
        publisher.publish(InvoicePaid(invoice_id=invoice.id))    committed with the invoice

The Feature keeps its one verb; whether `publish` sends now or keeps the event for the relay is
the wiring's choice.
"""

from typing import Any

from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.ddd.repositories import IRepository


class RepositoryQueue:
    def __init__(self, repository: IRepository) -> None:
        self.repository = repository

    def put(self, event: DomainEvent) -> list[Any] | None:
        self.repository.save([event])
        return None

    async def aput(self, event: DomainEvent) -> list[Any] | None:
        return self.put(event)
