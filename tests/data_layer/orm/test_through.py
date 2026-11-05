"""A filter that crosses a relation — `Through` — answered the same by memory and by SQL.

The cases are a shop: orders with lines (a foreign key, owned), a customer each order points at
(a to-one), tags an order shares with others (a many-to-many), lines that may be archived, and a
relation that only reads the open lines (its own scope). Every case asks one question of both
stores and expects the same orders back: some record the relation holds answers the selection.
"""

from dataclasses import dataclass, field
from decimal import Decimal

import pytest
from sqlalchemy import Column, ForeignKey, Integer, Numeric, Table, Text
from sqlalchemy.orm import registry

from sincpro_framework.data_layer.orm import Orphans, TableRelation, map_aggregates
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.repository import Repository
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.template_table import (
    archive_columns,
    entity_table,
)
from sincpro_framework.data_layer.repositories import MemoryRepository
from sincpro_framework.ddd import (
    All,
    Condition,
    Criteria,
    Not,
    Operator,
    Pagination,
    Specification,
    Through,
)
from sincpro_framework.ddd.entity import ArchivableMixin, Entity

from .engines import ENGINES, fresh


@dataclass
class ShopCustomer(Entity):
    name: str = ""
    city: str = ""


@dataclass
class ShopLine(ArchivableMixin, Entity):
    order_id: str = ""
    product: str = ""
    qty: int = 1
    state: str = "open"


@dataclass
class ShopTag(Entity):
    label: str = ""


@dataclass
class ShopOrder(Entity):
    number: str = ""
    total: Decimal = Decimal("0")
    customer_id: str = ""
    customer: ShopCustomer | None = None
    lines: list[ShopLine] = field(default_factory=list)
    open_lines: list[ShopLine] = field(default_factory=list)
    tags: list[ShopTag] = field(default_factory=list)


shop = registry()
customer_table = entity_table(
    "through_customer",
    shop.metadata,
    Column("name", Text, nullable=False),
    Column("city", Text, nullable=False),
)
tag_table = entity_table("through_tag", shop.metadata, Column("label", Text, nullable=False))
order_table = entity_table(
    "through_order",
    shop.metadata,
    Column("number", Text, nullable=False),
    Column("total", Numeric(10, 2), nullable=False),
    Column("customer_id", Text, ForeignKey("through_customer.id"), nullable=False),
)
line_table = entity_table(
    "through_line",
    shop.metadata,
    Column("order_id", Text, ForeignKey("through_order.id"), nullable=False),
    Column("product", Text, nullable=False),
    Column("qty", Integer, nullable=False),
    Column("state", Text, nullable=False),
    *archive_columns(),
)
order_tag = Table(
    "through_order_tag",
    shop.metadata,
    Column("order_id", Text, ForeignKey("through_order.id"), primary_key=True),
    Column("tag_id", Text, ForeignKey("through_tag.id"), primary_key=True),
)
map_aggregates(
    shop,
    {
        ShopCustomer: customer_table,
        ShopTag: tag_table,
        ShopOrder: order_table,
        ShopLine: line_table,
    },
    relations={
        ShopOrder: {
            "customer": TableRelation.foreign_key(ShopCustomer, identified_by="customer_id"),
            "lines": TableRelation.foreign_key(
                ShopLine, identified_by="order_id", orphans=Orphans.DELETE
            ),
            "open_lines": TableRelation.foreign_key(
                ShopLine,
                identified_by="order_id",
                owned=False,
                scope=Criteria(selection=Condition(field="state", value="open")),
            ),
            "tags": TableRelation.many_to_many(
                ShopTag, through=order_tag, this_key="order_id", related_key="tag_id"
            ),
        }
    },
)


def world() -> tuple[list[ShopCustomer], list[ShopTag], list[ShopOrder], list[ShopLine]]:
    ana, bo = ShopCustomer(name="Ana", city="La Paz"), ShopCustomer(name="Bo", city="Sucre")
    urgent, gift = ShopTag(label="urgent"), ShopTag(label="gift")
    first = ShopOrder(number="O-1", total=Decimal("100"), customer_id=ana.id)
    second = ShopOrder(number="O-2", total=Decimal("50"), customer_id=bo.id)
    third = ShopOrder(number="O-3", total=Decimal("20"), customer_id=ana.id)
    lines = [
        ShopLine(order_id=first.id, product="desk", qty=12),
        ShopLine(order_id=first.id, product="chair", qty=2, state="closed"),
        ShopLine(order_id=second.id, product="chair", qty=1),
        ShopLine(order_id=third.id, product="desk", qty=30, state="closed"),
    ]
    return [ana, bo], [urgent, gift], [first, second, third], lines


def in_memory() -> tuple[MemoryRepository, dict[str, str]]:
    customers, tags, orders, lines = world()
    by_id = {one.id: one for one in customers}
    first, second, third = orders
    for order in orders:
        order.customer = by_id[order.customer_id]
        order.lines = [one for one in lines if one.order_id == order.id]
        order.open_lines = [one for one in order.lines if one.state == "open"]
    first.tags, second.tags = [tags[0]], [tags[0], tags[1]]
    lines[3].archived_at = lines[3].created_at
    return MemoryRepository().add(*orders), {one.id: one.number for one in orders}


def in_sql(url: str) -> tuple[Repository, dict[str, str]]:
    store = Repository(fresh(url, shop.metadata))
    customers, tags, orders, lines = world()
    first, second, third = orders
    with store.context() as repository:
        repository.save([*customers, *tags])
        first.tags, second.tags = [tags[0]], [tags[0], tags[1]]
        repository.save(orders)
        repository.save(lines)
    with store.context() as repository:
        repository.archive(repository.get(ShopLine, lines[3].id))
    return store, {one.id: one.number for one in orders}


STORES = ["memory", *ENGINES]


@pytest.fixture(params=STORES)
def shop_store(request):
    return in_memory() if request.param == "memory" else in_sql(request.param)


def numbers(store, names: dict[str, str], selection) -> list[str]:
    page = store.search(ShopOrder, Criteria(selection=selection))
    assert page.dropped == ()
    return sorted(names[one.id] for one in page.items)


def test_some_line_answers_the_selection(shop_store):
    store, names = shop_store

    asked = Through(
        through="lines", selection=Condition(field="qty", operator=Operator.GT, value=10)
    )

    assert numbers(store, names, asked) == ["O-1"]


def test_an_archived_line_is_never_looked_at(shop_store):
    store, names = shop_store

    asked = Through(through="lines", selection=Condition(field="product", value="desk"))

    assert numbers(store, names, asked) == ["O-1"]


def test_the_relation_reads_with_its_own_scope(shop_store):
    store, names = shop_store

    asked = Through(through="open_lines", selection=Condition(field="product", value="chair"))

    assert numbers(store, names, asked) == ["O-2"]


def test_a_to_one_relation_is_crossed_too(shop_store):
    store, names = shop_store

    asked = Through(through="customer", selection=Condition(field="city", value="La Paz"))

    assert numbers(store, names, asked) == ["O-1", "O-3"]


def test_a_many_to_many_is_crossed_through_its_table(shop_store):
    store, names = shop_store

    asked = Through(through="tags", selection=Condition(field="label", value="gift"))

    assert numbers(store, names, asked) == ["O-2"]


def test_a_crossing_composes_with_the_record_s_own_conditions_and_with_not(shop_store):
    store, names = shop_store
    urgent = Through(through="tags", selection=Condition(field="label", value="urgent"))

    both = All(all=[urgent, Condition(field="total", operator=Operator.GT, value=60)])

    assert numbers(store, names, both) == ["O-1"]
    assert numbers(store, names, Not(negate=urgent)) == ["O-3"]


def test_a_crossing_read_from_json_answers_the_same(shop_store):
    store, names = shop_store

    asked = Criteria.model_validate(
        {
            "selection": {
                "through": "lines",
                "selection": {"field": "qty", "operator": ">", "value": 10},
            }
        }
    )

    assert sorted(names[one.id] for one in store.search(ShopOrder, asked).items) == ["O-1"]


def test_what_the_related_model_cannot_answer_is_dropped_by_its_path(shop_store):
    store, _ = shop_store

    page = store.search(
        ShopOrder,
        Criteria(
            selection=Through(
                through="lines", selection=Condition(field="colour", value="red")
            )
        ),
    )

    assert [(one.field, one.reason) for one in page.dropped] == [
        ("lines.colour", "unknown_field")
    ]


def test_a_relation_that_is_not_there_is_dropped(shop_store):
    store, _ = shop_store

    page = store.search(
        ShopOrder,
        Criteria(
            selection=Through(through="invoices", selection=Condition(field="x", value=1))
        ),
    )

    assert [(one.field, one.reason) for one in page.dropped] == [
        ("invoices", "unknown_field")
    ]


@pytest.mark.parametrize("url", ENGINES)
def test_a_relation_asked_with_a_page_of_none_brings_no_lines_and_filters_nothing(url):
    """A relation in the specification shapes what each record brings and never which records
    come: `limit: 0` brings no lines and every order. Filtering by a relation is `Through`.
    Memory holds each record with its relations whole, so this is the database's question."""
    store, names = in_sql(url)
    nothing = Specification(
        {"number": Criteria(), "lines": Criteria(pagination=Pagination(limit=0))}
    )

    page = store.search(ShopOrder, Criteria(specification=nothing))

    assert sorted(names[one.id] for one in page.items) == ["O-1", "O-2", "O-3"]
    assert all(len(one.lines) == 0 for one in page.items)
