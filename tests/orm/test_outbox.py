"""A whole outbox with no `EventRepository` class of its own: the four things one needs are
already here, each from a piece that existed before this feature.

    where it lives      the table you map your event class to, in the `Database` you choose
    the delivery state  `EventTrackableMixin` — pending/processing/acknowledged/failed, and the
                        `mark_*` calls that move between them
    claiming a batch    `search(..., for_update=True, skip_locked=True)`, so two relays never
                        take the same row
    finding one fast    an `Index` on the table, and `Criteria` to ask — the same one
                        everything else is asked with

What is genuinely missing from the framework is none of that: it is the relay loop itself, and
a relay is the twenty lines this test writes inline. Whether that deserves to ship as a class
is a decision about how much a project wants pre-made, not a gap in the vocabulary.
"""

from dataclasses import dataclass

import pytest
from sqlalchemy import Column, DateTime, Index, Integer, Text
from sqlalchemy.orm import registry

from sincpro_framework.ddd.criteria import Condition, Criteria, Operator
from sincpro_framework.ddd.entity import ChangeTrackingMixin, Entity, EntityUpdated
from sincpro_framework.ddd.entity.entity_collection import EntityCollection
from sincpro_framework.ddd.events import EventStatus, EventTrackableMixin
from sincpro_framework.orm.sqlalchemy.custom_fields import JsonText
from sincpro_framework.orm.sqlalchemy.data_mapper import entity_table, map_aggregates
from sincpro_framework.orm.sqlalchemy.database import Database
from sincpro_framework.orm.sqlalchemy.repository import Repository


@dataclass(kw_only=True)
class OrderUpdated(EventTrackableMixin, EntityUpdated):
    """The project's own event: what changed, plus where its delivery stands."""

    name = "sales.order.v1.updated"


@dataclass
class Order(ChangeTrackingMixin, Entity):
    reference: str
    state: str = "draft"

    change_event = OrderUpdated


class Outbox(EntityCollection[OrderUpdated]):
    pass


own = registry()

order_table = entity_table(
    "outbox_order",
    own.metadata,
    Column("reference", Text, nullable=False),
    Column("state", Text, nullable=False),
)

outbox_table = entity_table(
    "outbox",
    own.metadata,
    Column("label", JsonText, nullable=False),
    Column("entity_type", Text, nullable=False),
    Column("entity_id", Text, nullable=False),
    Column("correlation_id", Text),
    Column("causation_id", Text),
    Column("sequence", Integer, nullable=False),
    Column("changes", JsonText, nullable=False),
    # What `EventTrackableMixin` adds: the delivery state the relay reads and moves.
    Column("status", Text, nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("error_message", Text),
    Column("acknowledged_at", DateTime(timezone=True)),
    Column("failed_at", DateTime(timezone=True)),
    # The two readings a relay and a screen actually make, each with its own index.
    Index("outbox_pending", "status"),
    Index("outbox_by_record", "entity_type", "entity_id"),
)

map_aggregates(own, {Order: order_table, OrderUpdated: outbox_table})

PENDING = Criteria(
    where=Condition(field="status", operator=Operator.EQ, value=EventStatus.PENDING.value)
)


@pytest.fixture
def repository() -> Repository:
    database = Database("sqlite://")
    own.metadata.create_all(database.engine)
    return Repository(database)


def _change(repository: Repository, order: Order, state: str) -> None:
    """One ordinary use case: read, change, save — and leave the event in the outbox, in the
    same transaction that changed the row, which is the whole point."""
    with repository.context() as unit:
        loaded = unit.get(Order, order.id)
        assert loaded is not None
        loaded.state = state
        unit.save(loaded)
        for event in loaded.pull_events():
            unit.save(event)


def test_the_event_lands_pending_in_the_same_transaction_as_the_row(repository: Repository):
    order = Order(reference="SO-1")
    repository.save(order)

    _change(repository, order, "confirmed")

    waiting = repository.search(Outbox, PENDING)
    assert len(waiting) == 1
    # A `StrEnum` in a `Text` column comes back as the plain string it was stored as, not
    # as the enum member: compare by value, never with `is`.
    assert waiting.items[0].status == EventStatus.PENDING
    assert waiting.items[0].changes == {"state": ["draft", "confirmed"]}
    assert repository.get(Order, order.id).state == "confirmed"  # type: ignore[union-attr]


def test_a_relay_claims_dispatches_and_acknowledges(repository: Repository):
    order = Order(reference="SO-2")
    repository.save(order)
    _change(repository, order, "confirmed")

    dispatched: list[str] = []

    # The relay, whole: claim under a lock, hand over, acknowledge. Two of these running at
    # once never take the same row — that is what `skip_locked` buys.
    with repository.context() as unit:
        claimed = unit.search(Outbox, PENDING, for_update=True, skip_locked=True)
        for event in claimed:
            event.mark_processing()
            unit.save(event)
    for event in claimed:
        dispatched.append(event.name)  # a queue would go here
    with repository.context() as unit:
        for event in claimed:
            event.mark_acknowledged()
            unit.save(event)

    assert dispatched == ["sales.order.v1.updated"]
    assert len(repository.search(Outbox, PENDING)) == 0
    settled = repository.search(Outbox).items[0]
    assert settled.status == EventStatus.ACKNOWLEDGED
    assert settled.attempts == 1 and settled.acknowledged_at is not None


def test_a_failed_delivery_says_why_and_stays_to_be_retried(repository: Repository):
    order = Order(reference="SO-3")
    repository.save(order)
    _change(repository, order, "confirmed")

    with repository.context() as unit:
        for event in unit.search(Outbox, PENDING):
            event.mark_processing()
            event.mark_failed("the broker refused the connection")
            unit.save(event)

    failed = repository.search(Outbox).items[0]
    assert failed.status == EventStatus.FAILED
    assert failed.error_message == "the broker refused the connection"
    assert failed.attempts == 1 and failed.failed_at is not None


def test_the_history_of_one_record_is_the_same_criteria_over_the_same_table(
    repository: Repository,
):
    order = Order(reference="SO-4")
    repository.save(order)
    for state in ("confirmed", "shipped"):
        _change(repository, order, state)

    history = repository.search(
        Outbox,
        Criteria(where=Condition(field="entity_id", operator=Operator.EQ, value=order.id)),
    )

    assert [one.changes["state"] for one in history] == [
        ["confirmed", "shipped"],
        ["draft", "confirmed"],
    ]
