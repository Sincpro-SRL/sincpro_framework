"""`EventRelay`: delivers the deliverable events a bounded context kept — read from its
repository, handed to a publisher, marked — the transactional outbox's polling publisher.

    relay = EventRelay(repository, source=BillingDomainEvent, publisher=Publisher(kafka_queue))
    relay.run_once()      →  RelayPass(read=3, delivered=2, retried=1, parked=0, …)

**Over any repository.** It asks the repository the bounded context already has for the events
of `source` marked `DeliverableEventMixin` that are not delivered and are due — `delivered_at`
empty, `next_delivery_at` passed — oldest first. On a database it takes them inside a unit of
work with `FOR UPDATE SKIP LOCKED`, so replicas running the same relay never take the same
event, and marks them in the same transaction; a replica that dies before the commit leaves them
as they were, to be taken again.

**At least once.** An event that went out but was not marked — the process died in between —
goes out again; a consumer is idempotent (the inbox already is). The alternative to a duplicate
is a lost event.

**In order per entity.** Once an event of an entity is retried later, the later ones of that
entity in the pass wait behind it, so a consumer sees one entity's events in the order they
happened.

What drives it is the project's: a cron (`Crons.relay_deliverable_events`), a worker loop, a
Feature, a test. The relay holds no thread and no timer.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol

from sincpro_framework.ddd.criteria import (
    Condition,
    CountMode,
    Criteria,
    Operator,
    Pagination,
    combined,
    parse_order,
)
from sincpro_framework.ddd.entity import utc_now
from sincpro_framework.ddd.events import DeliverableEventMixin, DomainEvent
from sincpro_framework.ddd.exceptions import ContractViolation
from sincpro_framework.ddd.repositories import IRepository, Transacts
from sincpro_framework.event_driven.domain.failure import (
    DEFAULT_FAILURE_POLICY,
    DeliveryFailurePolicy,
    FailureOutcome,
    HandlingFailure,
    described,
)
from sincpro_framework.sincpro_abstractions import DataTransferObject
from sincpro_framework.sincpro_logger import logger


class Publishes(Protocol):
    """What the relay hands each event to — a `Publisher` over any queue is one."""

    def publish(self, event: DomainEvent) -> Any: ...


class RelayPass(DataTransferObject):
    """One pass: what was read, and what became of it."""

    read: int = 0
    delivered: int = 0
    retried: int = 0
    held: int = 0
    parked: int = 0
    skipped: int = 0


def deliverable_classes(source: type) -> list[type]:
    """`source` and every subclass of it marked `DeliverableEventMixin`."""
    found: list[type] = []
    pending = [source]
    while pending:
        cls = pending.pop(0)
        if issubclass(cls, DeliverableEventMixin) and cls not in found:
            found.append(cls)
        pending.extend(cls.__subclasses__())
    return found


def entity_of(event: DomainEvent) -> tuple[str, str] | None:
    """The entity an event is about, when it is about one — what keeps its order."""
    if event.entity_type and event.entity_id:
        return (event.entity_type, event.entity_id)
    return None


class EventRelay:
    """Delivers the deliverable events of `source` kept through `repository`."""

    def __init__(
        self,
        repository: IRepository,
        source: type[DomainEvent],
        publisher: Publishes,
        on_failure: DeliveryFailurePolicy = DEFAULT_FAILURE_POLICY,
        batch: int = 100,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        """`source` is the bounded context's base event class; its subclasses marked
        `DeliverableEventMixin` are what goes out. `clock` is the time, for a test to move."""
        if batch < 1:
            raise ContractViolation(f"a relay reads at least one event, not {batch}")
        self.repository = repository
        self.source = source
        self.publisher = publisher
        self.on_failure = on_failure
        self.batch = batch
        self.clock = clock

    def _due(self, now: datetime) -> Criteria:
        return Criteria(
            where=combined(
                Condition(field="delivered_at", operator=Operator.IS_NULL, value=True),
                Condition(field="next_delivery_at", operator=Operator.LTE, value=now),
            ),
            order=parse_order("id"),
            pagination=Pagination(limit=self.batch),
            count=CountMode.NONE,
        )

    def _claimed(self, repository: Any, now: datetime, locks: bool) -> list[Any]:
        """The due events, oldest first, at most a batch — locked, where the store locks."""
        found: dict[str, Any] = {}
        for cls in deliverable_classes(self.source):
            page = repository.search(cls, self._due(now), for_update=locks, skip_locked=locks)
            for event in page.items:
                found.setdefault(event.id, event)
        return sorted(found.values(), key=lambda event: event.id)[: self.batch]

    def run_once(self) -> RelayPass:
        """One pass: claim the due events, deliver them in order, mark them — one transaction
        on a store that has them."""
        now = self.clock()
        if isinstance(self.repository, Transacts):
            locks = getattr(self.repository, "capabilities").row_locks
            with self.repository.context() as unit:
                return self._carried_out(unit, now, locks)
        return self._carried_out(self.repository, now, False)

    def _carried_out(self, repository: Any, now: datetime, locks: bool) -> RelayPass:
        events = self._claimed(repository, now, locks)
        counts = {"delivered": 0, "retried": 0, "held": 0, "parked": 0, "skipped": 0}
        held: set[tuple[str, str]] = set()
        touched: list[Any] = []
        for event in events:
            entity = entity_of(event)
            if entity is not None and entity in held:
                counts["held"] += 1
                continue
            outcome, holds_entity, stops = self._delivered(event, now)
            touched.append(event)
            counts[outcome] += 1
            if holds_entity and entity is not None:
                held.add(entity)
            if stops:  # a retry in place: nothing after it goes first
                break
        if touched:
            repository.save(touched)
        return RelayPass(read=len(events), **counts)

    def _delivered(self, event: Any, now: datetime) -> tuple[str, bool, bool]:
        """What became of it: the outcome, whether its entity waits behind it, whether the pass
        stops at it."""
        try:
            self.publisher.publish(event)
        except (
            Exception
        ) as error:  # noqa: BLE001 - a failed delivery is decided, never raised
            return self._decided(event, error, now)
        event.delivered(now)
        return "delivered", False, False

    def _decided(self, event: Any, error: Exception, now: datetime) -> tuple[str, bool, bool]:
        failure = HandlingFailure(event, error, event.delivery_attempts + 1, now)
        decision = self.on_failure.decide(failure)
        reason = described(failure)
        logger.warning(
            f"{event.name} {event.id} failed (attempt {failure.attempts}), "
            f"{decision.outcome}: {reason}"
        )
        match decision.outcome:
            case FailureOutcome.RETRY_IN_PLACE:
                event.failed(reason, decision.retry_at or now)
                return "retried", True, True
            case FailureOutcome.RETRY_LATER:
                event.failed(reason, decision.retry_at or now)
                return "retried", decision.holds_stream, False
            case FailureOutcome.PARK:
                event.parked(reason)
                return "parked", False, False
            case FailureOutcome.SKIP:
                event.delivered(now)
                event.delivery = {**event.delivery, "skipped": reason}
                return "skipped", False, False
        return "retried", True, True
