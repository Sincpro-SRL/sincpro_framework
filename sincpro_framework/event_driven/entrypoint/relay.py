"""`EventRelay`: delivers the deliverable events a bounded context kept — read from its
repository, handed to a publisher, marked — the transactional outbox's polling publisher.

    relay = EventRelay(repository, source=BillingDomainEvent, publisher=Publisher(kafka_queue))
    relay.run_once()      →  RelayPass(read=3, delivered=2, retried=1, parked=0, …)

**Over any repository.** One query asks the repository the bounded context already has for the
events of `source` marked `DeliverableEventMixin` that are not delivered and are due —
`delivered_at` empty, `next_delivery_at` passed — oldest first, at most a batch. On a database it
takes them inside a unit of work with `FOR UPDATE SKIP LOCKED`, so replicas running the same relay
never take the same event, and marks them in the same transaction; a replica that dies before the
commit leaves them as they were, to be taken again.

**At least once.** An event that went out but was not marked — the process died in between —
goes out again, so every consumer is idempotent (the inbox already is). The alternative to a
duplicate is a lost event.

**In order, across passes.** An event waiting for its retry holds back, pass after pass, what its
policy says (`Holds`): every event after it (`RetryInPlace`), or the later events of its entity
(`RetryLater`). A parked event holds nothing — like a dead-letter queue, its entity's later
events go on without it, and a replayed one goes out after them.

What drives it is the project's: a cron (`Crons.relay_deliverable_events`), a worker loop, a
Feature, a test. The relay holds no thread and no timer.
"""

from collections.abc import Callable
from datetime import datetime
from enum import StrEnum
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
from sincpro_framework.ddd.repositories import IRepository, Transacts
from sincpro_framework.event_driven.domain.failure import (
    DEFAULT_FAILURE_POLICY,
    DeliveryFailurePolicy,
    FailureOutcome,
    HandlingFailure,
    Holds,
)
from sincpro_framework.exceptions import ProgrammingError
from sincpro_framework.sincpro_abstractions import DataTransferObject
from sincpro_framework.sincpro_logger import logger


class Publishes(Protocol):
    """What the relay hands each event to — a `Publisher` over any queue is one."""

    def publish(self, event: DomainEvent) -> Any: ...


class Became(StrEnum):
    """What became of one event in a pass — a field of `RelayPass` each."""

    DELIVERED = "delivered"
    RETRIED = "retried"
    HELD = "held"
    PARKED = "parked"
    SKIPPED = "skipped"


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


class EventRelay:
    """Delivers the deliverable events of `source` kept through `repository`."""

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
        given. `clock` is the time, for a test to move."""
        if batch < 1:
            raise ProgrammingError(f"a relay reads at least one event, not {batch}")
        self.repository = repository
        self.source = source
        self.publisher = publisher
        self.on_failure = on_failure or DEFAULT_FAILURE_POLICY
        self.batch = batch
        self.clock = clock

    def _search(
        self, repository: Any, conditions: list[Condition], limit: int, locks: bool
    ) -> list[Any]:
        """The not-delivered deliverable events these conditions select, oldest first."""
        names = [cls.name for cls in deliverable_classes(self.source)]
        criteria = Criteria(
            where=combined(
                # the name first: an event of the table that is not deliverable has no
                # delivery to read
                Condition(field="name", operator=Operator.IN, value=names),
                Condition(field="delivered_at", operator=Operator.IS_NULL, value=True),
                *conditions,
            ),
            order=parse_order("id"),
            pagination=Pagination(limit=limit),
            count=CountMode.NONE,
        )
        return list(
            repository.search(
                self.source, criteria, for_update=locks, skip_locked=locks
            ).items
        )

    def run_once(self) -> RelayPass:
        """One pass: claim the due events, deliver them in order, mark them — one transaction
        on a store that has them."""
        now = self.clock()
        if isinstance(self.repository, Transacts):
            with self.repository.context() as unit:
                return self._carried_out(unit, now, self.repository.capabilities.row_locks)
        return self._carried_out(self.repository, now, False)

    def _carried_out(self, repository: Any, now: datetime, locks: bool) -> RelayPass:
        """1. The due events, oldest first, at most a batch — locked, where the store locks.
        2. What waits for its retry holds back what the policy says:
           2.1 everything after it — a claimed event newer than the oldest waiting one is held,
               and so is every one after it;
           2.2 its entity — a claimed event of an entity with one waiting is held.
        3. Each one delivered, or decided by the policy; an event of a held entity waits, and
           a retry in place ends the pass.
        4. Final: every event touched is saved in the same unit of work."""
        waiting = Condition(field="next_delivery_at", operator=Operator.GT, value=now)
        due = Condition(field="next_delivery_at", operator=Operator.LTE, value=now)
        events = self._search(repository, [due], self.batch, locks)

        blocking: str | None = None
        held: set[tuple[str, str]] = set()
        if events and self.on_failure.holds == Holds.EVERYTHING:
            oldest = self._search(repository, [waiting], 1, False)
            blocking = oldest[0].id if oldest else None
        if events and self.on_failure.holds == Holds.ENTITY:
            ids = sorted({event.entity_id for event in events if event.entity_id})
            if ids:
                about = Condition(field="entity_id", operator=Operator.IN, value=ids)
                held = {
                    (event.entity_type, event.entity_id)
                    for event in self._search(repository, [waiting, about], self.batch, False)
                }

        counts = {became: 0 for became in Became}
        touched: list[Any] = []
        for position, event in enumerate(events):
            if blocking is not None and event.id > blocking:
                counts[Became.HELD] += len(events) - position
                break
            entity = (event.entity_type, event.entity_id) if event.entity_id else None
            if entity is not None and entity in held:
                counts[Became.HELD] += 1
                continue
            became, holds_entity, stops = self._delivered(event, now)
            touched.append(event)
            counts[became] += 1
            if holds_entity and entity is not None:
                held.add(entity)
            if stops:
                break
        if touched:
            repository.save(touched)
        return RelayPass(read=len(events), **{str(became): n for became, n in counts.items()})

    def _delivered(self, event: Any, now: datetime) -> tuple[Became, bool, bool]:
        """What became of it: the outcome, whether its entity waits behind it, whether the pass
        stops at it."""
        try:
            self.publisher.publish(event)
        except (
            Exception
        ) as error:  # noqa: BLE001 - a failed delivery is decided, never raised
            return self._decided(event, error, now)
        event.delivered(now)
        return Became.DELIVERED, False, False

    def _decided(
        self, event: Any, error: Exception, now: datetime
    ) -> tuple[Became, bool, bool]:
        failure = HandlingFailure(event, error, event.delivery_attempts + 1, now)
        decision = self.on_failure.decide(failure)
        reason = f"{type(error).__name__}: {error}"
        logger.warning(
            f"{event.name} {event.id} failed (attempt {failure.attempts}), "
            f"{decision.outcome}: {reason}"
        )
        match decision.outcome:
            case FailureOutcome.RETRY_IN_PLACE:
                event.failed(reason, decision.retry_at or now)
                return Became.RETRIED, True, True
            case FailureOutcome.RETRY_LATER:
                event.failed(reason, decision.retry_at or now)
                return Became.RETRIED, decision.holds_stream, False
            case FailureOutcome.PARK:
                event.parked(reason)
                return Became.PARKED, False, False
            case FailureOutcome.SKIP:
                event.delivered(now)
                event.delivery = {**event.delivery, "skipped": reason}
                return Became.SKIPPED, False, False
