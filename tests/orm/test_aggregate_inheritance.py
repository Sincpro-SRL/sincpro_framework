"""An aggregate that extends another one keeps what it inherited.

A subclass mapped to a table of its own used to be mapped alone: its row held only its own
columns, and the fields it inherited came back as their defaults — `number=''`, `total=0` —
without an error anywhere. It is mapped as an extension of its parent now (joined-table
inheritance): the inherited fields live in the parent's table, its own in its table.
"""

from dataclasses import dataclass

import pytest
from sqlalchemy import Column, ForeignKey, Integer, Table, Text
from sqlalchemy.orm import registry

from sincpro_framework.ddd import Criteria, Entity, EntityCollection, StaleAggregate
from sincpro_framework.orm import Database, Repository, describe, entity_table, map_aggregates


@dataclass
class Invoice(Entity):
    number: str = ""
    total: int = 0


@dataclass
class DiscountedInvoice(Invoice):
    discount: int = 0


class DiscountedInvoices(EntityCollection[DiscountedInvoice]): ...


shelf = registry()
invoice_table = entity_table(
    "invoice",
    shelf.metadata,
    Column("number", Text, nullable=False),
    Column("total", Integer, nullable=False),
)
discounted_table = Table(
    "discounted_invoice",
    shelf.metadata,
    Column("id", Text, ForeignKey("invoice.id"), primary_key=True),
    Column("discount", Integer, nullable=False),
)
# The child first, on purpose: the order of the dict must not decide who extends whom.
map_aggregates(shelf, {DiscountedInvoice: discounted_table, Invoice: invoice_table})


def _stored(repository: Repository, invoice_id: str) -> DiscountedInvoice:
    stored = repository.get(DiscountedInvoice, invoice_id)
    assert stored is not None
    return stored


@pytest.fixture
def repository() -> Repository:
    database = Database("sqlite://")
    shelf.metadata.create_all(database.engine)
    return Repository(database)


def test_a_subclass_keeps_the_fields_it_inherited(repository: Repository):
    invoice = DiscountedInvoice(number="F-2", total=300, discount=7)
    repository.save(invoice)

    stored = _stored(repository, invoice.id)

    assert (stored.number, stored.total, stored.discount) == ("F-2", 300, 7)


def test_a_subclass_is_versioned_like_its_parent(repository: Repository):
    invoice = DiscountedInvoice(number="F-2", total=300, discount=7)
    repository.save(invoice)
    mine = _stored(repository, invoice.id)
    theirs = _stored(repository, invoice.id)

    mine.total = 310
    repository.save(mine)
    theirs.total = 999

    assert _stored(repository, invoice.id).version == 2
    with pytest.raises(StaleAggregate):
        repository.save(theirs)


def test_a_criteria_reads_inherited_and_own_fields_alike(repository: Repository):
    repository.save(
        [
            DiscountedInvoice(number="F-1", total=100, discount=0),
            DiscountedInvoice(number="F-2", total=300, discount=7),
        ]
    )

    page = repository.search(
        DiscountedInvoices,
        Criteria.model_validate(
            {
                "where": {
                    "all": [
                        {"field": "total", "operator": ">", "value": 200},
                        {"field": "discount", "value": 7},
                    ]
                },
                "order": [{"field": "number"}],
            }
        ),
    )

    assert [invoice.number for invoice in page.items] == ["F-2"]
    assert page.dropped == ()


def test_the_definition_of_a_subclass_lists_what_it_inherited():
    assert {"number", "total", "discount", "version"} <= set(
        describe(DiscountedInvoice).fields
    )


def test_a_subclass_table_that_does_not_reference_its_parent_is_refused():
    @dataclass
    class Quote(Entity):
        number: str = ""

    @dataclass
    class RushQuote(Quote):
        fee: int = 0

    other = registry()
    quote_table = entity_table("quote", other.metadata, Column("number", Text))
    unrelated = Table(
        "rush_quote",
        other.metadata,
        Column("id", Text, primary_key=True),
        Column("fee", Integer),
    )

    with pytest.raises(ValueError, match="RushQuote extends Quote.*quote.id"):
        map_aggregates(other, {Quote: quote_table, RushQuote: unrelated})
