"""A to-many declared `EntityCollection[Line]` — the recommended type — mapped, read, edited with
`append`, `remove` and `replace`, and saved whole; `list[Line]` keeps working beside it.

Runs on every engine the suite has (`engine_url`).
"""

from collections.abc import Generator
from dataclasses import dataclass, field, replace
from typing import Any

import pytest
from sqlalchemy import Column, ForeignKey, Integer, Text
from sqlalchemy.orm import registry

from sincpro_framework.data_layer.orm import Orphans, TableRelation, map_aggregates
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.repository import Repository
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.template_table import entity_table
from sincpro_framework.ddd import apply_values, exceptions
from sincpro_framework.ddd.criteria import Criteria, Pagination, Sort, Specification
from sincpro_framework.ddd.entity import Entity
from sincpro_framework.ddd.entity.entity_collection import Count, EntityCollection
from sincpro_framework.ddd.query import ResponsePaginatedQuery

from .engines import fresh


@dataclass
class Item(Entity):
    sale_id: str = ""
    qty: int = 0


@dataclass
class Sale(Entity):
    number: int = 0
    items: EntityCollection[Item] = field(default_factory=EntityCollection)


@dataclass
class Note(Entity):
    ticket_id: str = ""
    text: str = ""


@dataclass
class Ticket(Entity):
    title: str = ""
    notes: list[Note] = field(default_factory=list)


class Sales(EntityCollection[Sale]):
    pass


@dataclass
class Card(Entity):
    board_id: str = ""
    points: int = 0


class Cards(EntityCollection[Card]):
    def points(self) -> int:
        return sum(card.points for card in self)


@dataclass
class Board(Entity):
    name: str = ""
    cards: Cards = field(default_factory=Cards)


class ResponseSales(ResponsePaginatedQuery):
    sales: EntityCollection[Sale]


class ResponseSaleList(ResponsePaginatedQuery):
    sales: list[Sale]


shop = registry()
sale_table = entity_table("c_sale", shop.metadata, Column("number", Integer, nullable=False))
item_table = entity_table(
    "c_item",
    shop.metadata,
    Column("sale_id", Text, ForeignKey("c_sale.id"), nullable=False),
    Column("qty", Integer, nullable=False),
)
ticket_table = entity_table("c_ticket", shop.metadata, Column("title", Text, nullable=False))
board_table = entity_table("c_board", shop.metadata, Column("name", Text, nullable=False))
card_table = entity_table(
    "c_card",
    shop.metadata,
    Column("board_id", Text, ForeignKey("c_board.id"), nullable=False),
    Column("points", Integer, nullable=False),
)
note_table = entity_table(
    "c_note",
    shop.metadata,
    Column("ticket_id", Text, ForeignKey("c_ticket.id"), nullable=False),
    Column("text", Text, nullable=False),
)
map_aggregates(
    shop,
    {
        Sale: sale_table,
        Item: item_table,
        Ticket: ticket_table,
        Note: note_table,
        Board: board_table,
        Card: card_table,
    },
    relations={
        Sale: {
            "items": TableRelation.foreign_key(
                Item, identified_by="sale_id", orphans=Orphans.DELETE
            )
        },
        Ticket: {
            "notes": TableRelation.foreign_key(
                Note, identified_by="ticket_id", orphans=Orphans.DELETE
            )
        },
    },
)


@pytest.fixture
def repository(engine_url) -> Generator[Repository]:
    database = fresh(engine_url, shop.metadata)
    yield Repository(database)
    database.engine.dispose()


def got[T](repository: Repository, kind: type[T], identity: Any) -> T:
    record = repository.get(kind, identity)
    assert record is not None
    return record


def quantities(collection: Any) -> list[int]:
    return sorted(one.qty for one in collection)


def test_a_sale_built_with_a_list_is_saved_whole_and_read_back_as_its_collection(repository):
    sale = Sale(number=1, items=[Item(qty=1), Item(qty=2)])  # type: ignore[arg-type]
    repository.save(sale)

    with repository.context() as unit:
        read = got(unit, Sale, sale.id)

        assert isinstance(read.items, EntityCollection)
        assert quantities(read.items) == [1, 2]
        assert read.items.count == Count(value=2, exact=True) and not read.items.is_partial


def test_append_remove_and_replace_are_saved_as_the_sale_now_holds_them(repository):
    first, second = Item(qty=1), Item(qty=2)
    sale = Sale(number=2, items=[first, second])  # type: ignore[arg-type]
    repository.save(sale)

    with repository.context() as unit:
        read = got(unit, Sale, sale.id)
        read.items = (
            read.items.append(Item(qty=3)).remove(first).replace(replace(second, qty=20))
        )
        unit.save(read)

    with repository.context() as unit:
        assert quantities(got(unit, Sale, sale.id).items) == [3, 20]


def test_a_copy_read_before_another_writer_saved_its_row_is_refused_as_stale(repository):
    line = Item(qty=1)
    sale = Sale(number=6, items=[line])  # type: ignore[arg-type]
    repository.save(sale)
    with repository.context() as unit:
        stale = got(unit, Sale, sale.id).items[0]
        copy = replace(stale, qty=50)
    with repository.context() as unit:
        moved = got(unit, Item, line.id)
        moved.qty = 2
        unit.save(moved)

    with pytest.raises(exceptions.StaleAggregate, match="changed since this copy"):
        with repository.context() as unit:
            read = got(unit, Sale, sale.id)
            read.items = read.items.replace(copy)
            unit.save(read)


def test_a_page_read_for_a_client_keeps_its_count_and_cursor(repository):
    sale = Sale(number=3, items=[Item(qty=n) for n in range(1, 8)])  # type: ignore[arg-type]
    repository.save(sale)
    asked = Criteria(
        specification=Specification(
            {"items": Criteria(order=Sort.parse("-qty"), pagination=Pagination(limit=3))}
        )
    )

    page = repository.search(Sales, asked)
    answer = ResponseSales.of(page, asked)

    items = page.items[0].items
    assert isinstance(items, EntityCollection)
    assert [one.qty for one in items] == [7, 6, 5]
    assert items.count == Count(value=7, exact=True) and items.cursor is not None
    assert isinstance(answer.sales, EntityCollection)
    assert answer.sales.count == page.count


def test_a_response_declares_a_collection_or_a_list_and_travels_the_same(repository):
    repository.save(Sale(number=4, items=[Item(qty=1)]))  # type: ignore[arg-type]
    asked = Criteria(specification=Specification({"number": Criteria(), "items": Criteria()}))
    page = repository.search(Sales, asked)

    as_collection = ResponseSales.of(page, asked)
    as_list = ResponseSaleList.of(page, asked)

    assert isinstance(as_list.sales, list)
    wire = as_collection.model_dump(mode="json")
    assert wire == {**as_list.model_dump(mode="json")}
    assert wire["sales"][0]["items"]["count"] == {"value": 1, "exact": True}


def test_a_form_sends_the_collection_back_whole_and_it_is_held_as_one(repository):
    sale = Sale(number=5, items=[Item(qty=1)])  # type: ignore[arg-type]
    repository.save(sale)

    with repository.context() as unit:
        read = got(unit, Sale, sale.id)
        apply_values(
            read,
            {
                "items": {
                    "items": [{"qty": 8}, {"qty": 9}],
                    "count": {"value": 2, "exact": True},
                }
            },
        )
        unit.save(read)

        assert isinstance(read.items, EntityCollection)

    with repository.context() as unit:
        assert quantities(got(unit, Sale, sale.id).items) == [8, 9]


def test_a_relation_declared_as_a_list_keeps_working_beside_it(repository):
    ticket = Ticket(title="t", notes=[Note(text="a"), Note(text="b")])
    repository.save(ticket)

    with repository.context() as unit:
        read = got(unit, Ticket, ticket.id)
        read.notes = [*read.notes, Note(text="c")]
        unit.save(read)

    with repository.context() as unit:
        assert sorted(one.text for one in got(unit, Ticket, ticket.id).notes) == [
            "a",
            "b",
            "c",
        ]


def test_a_relation_declared_as_a_subclass_is_read_as_it(repository):
    board = Board(name="b", cards=[Card(points=3), Card(points=5)])  # type: ignore[arg-type]
    repository.save(board)

    with repository.context() as unit:
        read = got(unit, Board, board.id)

        assert isinstance(read.cards, Cards) and read.cards.points() == 8

    page = repository.search(
        Board, Criteria(specification=Specification({"cards": Criteria()}))
    )
    assert isinstance(page.items[0].cards, Cards)
