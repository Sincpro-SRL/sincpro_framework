"""`EventRelay`: delivers the deliverable events a bounded context saved — read from its
repository, handed to a publisher, marked — the transactional outbox's polling publisher.

    relay = EventRelay(repository, source=BillingDomainEvent, publisher=Publisher(kafka_queue))
    relay.run_once()      →  RelayPass(read=3, delivered=2, retried=1, parked=0, …)

The work is `EventRelaying`'s: one query over any repository for the due events, locked where
the store locks, delivered in order, decided by the failure policy, marked in the same
transaction — at least once.

What drives it is the project's: a cron (`Crons.relay_deliverable_events`), a worker loop, a
Feature, a test. The relay holds no thread and no timer.
"""

from collections.abc import Callable
from datetime import datetime

from sincpro_framework.ddd.entity import utc_now
from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.ddd.repositories import IRepository
from sincpro_framework.event_driven.domain.failure import DeliveryFailurePolicy
from sincpro_framework.event_driven.domain.relay import Publishes, RelayPass
from sincpro_framework.event_driven.services.relaying import EventRelaying


class EventRelay:
    """Delivers the deliverable events of `source` saved through `repository`."""

    def __init__(
        self,
        repository: IRepository,
        source: type[DomainEvent],
        publisher: Publishes,
        on_failure: DeliveryFailurePolicy | None = None,
        batch: int = 100,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        """`source` is the bounded context's base event class; its subclasses marked
        `DeliverableEventMixin` are what goes out. `publisher` is a `Publisher` over any queue,
        or anything with its `publish(event)`. `on_failure` is `DEFAULT_FAILURE_POLICY` unless
        given. `clock` is the time, for a test to move.

            EventRelay(events_repository, BillingEvent, Publisher(queue))
                ->  100 events a pass, `DEFAULT_FAILURE_POLICY`, the real clock
            EventRelay(events_repository, BillingEvent, Publisher(queue), batch=0)
                ->  raises ProgrammingError("a relay reads at least one event, not 0")

        Nothing is read here; `run_once` reads, delivers and marks.
        """
        self.source = source
        self._relaying = EventRelaying(
            repository, source, publisher, on_failure, batch, clock
        )

    def run_once(self) -> RelayPass:
        """One pass: claim the due events, deliver them in order, mark them — one transaction
        on a store that has them.

            relay.run_once()   ->  RelayPass(read=3, delivered=2, retried=1)
            relay.run_once()   # nothing due  ->  RelayPass()   — every count 0
        """
        return self._relaying.run_once()
