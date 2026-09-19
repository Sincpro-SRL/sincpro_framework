"""Storing the events an aggregate recorded needs no `EventRepository` class of its own.

`DomainEvent` inherits `Entity` — it has an `id`, a `created_at` and a `version` — so the
`Repository` that already exists persists and queries it like any other aggregate: map the
event class to a table, `save()` what `pull_events()` handed over, `search()` it back with the
same `Criteria` everything else uses. What a project calls its "event store" is a table and
this repository, not a second abstraction.

Delivery is the other half and is NOT here: a `Queue` hands an event to whoever reacts to it.
A store answers "what happened to this record"; a queue makes something else happen. A project
can have either, both or neither.
"""

from dataclasses import dataclass

import pytest
from sqlalchemy import Column, Integer, Text
from sqlalchemy.orm import registry

from sincpro_framework.ddd.criteria import Condition, Criteria, Operator
from sincpro_framework.ddd.entity import ChangeTrackingMixin, Entity, EntityUpdated
from sincpro_framework.ddd.entity.entity_collection import EntityCollection
from sincpro_framework.orm.sqlalchemy.custom_fields import JsonText
from sincpro_framework.orm.sqlalchemy.data_mapper import entity_table, map_aggregates
from sincpro_framework.orm.sqlalchemy.database import Database
from sincpro_framework.orm.sqlalchemy.repository import Repository


@dataclass(kw_only=True)
class InvoiceUpdated(EntityUpdated):
    """The project's own event class, and what gets mapped — **never the framework's generic
    `EntityUpdated` itself**: mapping a class makes every subclass of it part of that mapper's
    hierarchy, so a second event class declared anywhere else in the process would be dragged
    into this table."""

    name = "billing.invoice.v1.updated"


@dataclass
class Invoice(ChangeTrackingMixin, Entity):
    number: str
    state: str = "draft"

    change_event = InvoiceUpdated


class InvoiceUpdates(EntityCollection[InvoiceUpdated]):
    pass


own = registry()

invoice_table = entity_table(
    "stored_invoice",
    own.metadata,
    Column("number", Text, nullable=False),
    Column("state", Text, nullable=False),
)

# The event's own table: the four `Entity` columns, the `DomainEvent` envelope, and what this
# event adds. Nothing here is special-cased — it is an aggregate like the invoice beside it.
event_table = entity_table(
    "domain_event",
    own.metadata,
    Column("label", JsonText, nullable=False),
    Column("entity_type", Text, nullable=False),
    Column("entity_id", Text, nullable=False),
    Column("correlation_id", Text),
    Column("causation_id", Text),
    Column("sequence", Integer, nullable=False),
    Column("changes", JsonText, nullable=False),
)

map_aggregates(own, {Invoice: invoice_table, InvoiceUpdated: event_table})


@pytest.fixture
def repository() -> Repository:
    database = Database("sqlite://")
    own.metadata.create_all(database.engine)
    return Repository(database)


def test_an_event_is_saved_by_the_repository_that_already_exists(repository: Repository):
    invoice = Invoice(number="F-1")
    repository.save(invoice)

    loaded = repository.get(Invoice, invoice.id)
    assert loaded is not None
    loaded.state = "posted"
    repository.save(loaded)

    for event in loaded.pull_events():
        repository.save(event)

    stored = repository.search(InvoiceUpdates)
    assert len(stored) == 1
    assert stored.items[0].entity_type == "Invoice"
    assert stored.items[0].entity_id == invoice.id
    # A tuple has no JSON of its own: it round-trips through the column as a list.
    assert stored.items[0].changes == {"state": ["draft", "posted"]}


def test_the_history_of_one_record_is_an_ordinary_criteria(repository: Repository):
    invoice = Invoice(number="F-2")
    repository.save(invoice)

    for state in ("posted", "paid"):
        loaded = repository.get(Invoice, invoice.id)
        assert loaded is not None
        loaded.state = state
        repository.save(loaded)
        for event in loaded.pull_events():
            repository.save(event)

    history = repository.search(
        InvoiceUpdates,
        Criteria(where=Condition(field="entity_id", operator=Operator.EQ, value=invoice.id)),
    )

    assert [one.changes["state"] for one in history] == [
        ["posted", "paid"],
        ["draft", "posted"],
    ]  # newest first: the default order is `-id`, and a uuid7 sorts by when it was minted
