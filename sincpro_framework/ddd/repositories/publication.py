"""`PublishOnCommit`: what the aggregates of a write recorded, handed to a publisher once the write
committed — and the stored event stamped with how its delivery went.

    Repository(database, hooks, publish_on_commit=rabbit)            every deliverable event
    Repository(database, publish_on_commit=PublishOnCommit(rabbit, entities=[Invoice],
                                                           when=lambda event: ...))

    class InvoiceEvents(PublishOnCommit):                            to measure
        def publishes(self, event): ...                             what goes out
        def publisher_of(self, event): ...                          and where

**After the commit, never before.** Inside a unit of work the events wait for its `after_commit`,
and a rollback drops them; outside one every save commits on its own, so they go out as it
returns. Which events, in what order, and whether a failure matters is the project's to say in its
Feature; this is the default way, opted into per repository — where each bounded context already
wires its store.

**Taken, once.** What goes out is pulled from the aggregate and the rest recorded back, so a later
save — or the Feature's own `pull_events()` — never hands the same event on twice.

**Stamped where it is stored.** A `DeliverableEventMixin` event the repository stored is marked
delivered or failed in a unit of work of its own: at once for a publisher that answered in the
call, when its `Future` resolves for one that forwards later. A publisher that raises never undoes
the commit: its error is logged and stamped.
"""

from collections.abc import Callable, Sequence
from concurrent.futures import Future
from contextlib import nullcontext
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

from sincpro_framework.ddd import exceptions
from sincpro_framework.ddd.entity import utc_now
from sincpro_framework.ddd.events import DeliverableEventMixin, DomainEvent
from sincpro_framework.ddd.repositories.capabilities import Transacts
from sincpro_framework.sincpro_logger import logger

if TYPE_CHECKING:
    from sincpro_framework.ddd.repositories.repository import IRepository


class Publishes(Protocol):
    """What the events are handed to — a `Publisher`, or anything with its `publish`."""

    def publish(self, message: Any, /) -> Any:
        """Send `message` on: an answer, nothing, or a `Future` of a delivery still to come."""
        ...


class PublishOnCommit:
    """Hands the events a write recorded to a publisher once it committed. See the module."""

    def __init__(
        self,
        publisher: Publishes,
        entities: Sequence[type] = (),
        when: Callable[[DomainEvent], bool] | None = None,
    ) -> None:
        """`entities` narrows to the events those aggregates recorded; `when` to the events it
        admits — both on top of the mark.

            PublishOnCommit(rabbit)                                  every deliverable event
            PublishOnCommit(rabbit, entities=[Invoice, Payment])     theirs only
            PublishOnCommit(rabbit, when=lambda event: event.entity_id != "")
        """
        self.publisher = publisher
        self.entities = tuple(entities)
        self.when = when

    def publishes(self, event: DomainEvent) -> bool:
        """Whether `event` goes out: marked `DeliverableEventMixin`, recorded by one of
        `entities` when they were given, and admitted by `when`. Override to say otherwise."""
        return (
            isinstance(event, DeliverableEventMixin)
            and (
                not self.entities
                or event.entity_type in {one.__name__ for one in self.entities}
            )
            and (self.when is None or self.when(event))
        )

    def publisher_of(self, event: DomainEvent) -> Publishes:
        """Where `event` goes — the one publisher given. Override for one per event; a channel
        per event is the queue's `channel_of`, not another publisher."""
        return self.publisher

    def _taken(self, records: list[Any], newness: list[bool]) -> list[DomainEvent]:
        """The events of a write that go out, in the order they were recorded.

        1. An event saved as a record of its own goes out when it was never tried: a
           deliverable one with no attempt yet — its stamp, saved later, has one — any other
           when the write inserted it.
        2. An aggregate's events are pulled; what does not go out is recorded back on it.

        Context: an event's table has no version, so a stored event read back looks new; what
        it says about its own delivery is what tells a stamp from a first save.
        """
        taken: list[DomainEvent] = []
        for one, is_new in zip(records, newness):
            if isinstance(one, DomainEvent):
                untried = (
                    one.delivery_attempts == 0
                    if isinstance(one, DeliverableEventMixin)
                    else is_new
                )
                if untried and self.publishes(one):
                    taken.append(one)
                continue
            pull = getattr(one, "pull_events", None)
            if pull is None:
                continue
            for event in pull():
                if self.publishes(event):
                    taken.append(event)
                else:
                    one.record(event)
        return taken

    def _stamped(
        self,
        repository: "IRepository",
        event: DomainEvent,
        delivered_at: datetime | None,
        attempts: int,
        error: str | None,
    ) -> None:
        """The stored copy of `event` marked delivered at `delivered_at`, or failed with
        `error` — in a unit of work of its own, where the store has them.

        Context: an event that is not marked, or whose kind this store does not hold — an event
        class with no table — has nothing to stamp. A stamp that fails is logged and nothing
        more: the delivery already happened.
        """
        if not isinstance(event, DeliverableEventMixin) or not repository._keeps(type(event)):
            return
        opened = (
            repository.context()
            if isinstance(repository, Transacts)
            else nullcontext(repository)
        )
        try:
            with opened as store:
                stored = store.get(type(event), event.id)
                if stored is None:
                    return
                if delivered_at is not None:
                    stored.delivered(delivered_at, attempts)
                else:
                    stored.failed(error or "not delivered", attempts)
                store.save(stored)
        except Exception:  # noqa: BLE001 - a stamp never undoes a delivery
            logger.exception(f"the delivery of {event.name} {event.id} could not be stamped")

    def _handed(self, repository: "IRepository", event: DomainEvent) -> None:
        """`event` to its publisher, and stamped as it went: at once, or once the `Future` the
        publisher answered resolves to its `Forwarded` (`delivered_at`, `attempts`,
        `last_error`)."""
        try:
            answered = self.publisher_of(event).publish(event)
        except Exception as error:  # noqa: BLE001 - the commit stands, whatever it raised
            reason = f"{type(error).__name__}: {error}"
            logger.warning(
                f"{event.name} {event.id} was not published after the commit: {reason}"
            )
            self._stamped(repository, event, None, 1, reason)
            return
        if not isinstance(answered, Future):
            self._stamped(repository, event, utc_now(), 1, None)
            return

        def forwarded(done: Future) -> None:
            """The forwarding's report, stamped — read by its fields, whatever forwarded it."""
            report = done.result()
            self._stamped(
                repository, event, report.delivered_at, report.attempts, report.last_error
            )

        answered.add_done_callback(forwarded)

    def saved(
        self, repository: "IRepository", records: list[Any], newness: list[bool]
    ) -> None:
        """What a write of `repository` recorded, handed on once it committed — called by the
        repository after every write, `newness` saying which records it inserted.

        1. The events that go out, taken from the records (`_taken`); none, nothing to do.
        2. Inside a unit of work: handed on by its `after_commit`, dropped with its rollback.
        Final: outside one the write already committed, so they are handed on now — through the
           repository it is a view of, since the unit's session is over by the time they are.
        """
        events = self._taken(records, newness)
        if not events:
            return
        origin = repository._guard

        def handed() -> None:
            """Each event to its publisher, in the order it was recorded."""
            for event in events:
                self._handed(origin, event)

        if isinstance(repository, Transacts):
            try:
                repository.after_commit(handed)
                return
            except exceptions.ProgrammingError:
                pass  # outside a unit of work: the write committed on its own
        handed()
