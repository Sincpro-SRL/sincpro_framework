"""Lines a form sends, end to end on a database: a new order previewed and created with lines
that do not know the order's key, then edited — one line changed, one dropped, one added.

A form never knows the key a line holds pointing back at its order, least of all for an order
that is not stored yet. `assign` fills it from the relation, so the form sends only what a
person types. Dropping a line is what the relation says it is: here `Orphans.DELETE`.
"""

from dataclasses import dataclass, field
from decimal import Decimal

import pytest
from sqlalchemy import Column, ForeignKey, Integer, MetaData, Numeric, Text
from sqlalchemy.orm import registry

from sincpro_framework import UseFramework
from sincpro_framework.data_layer.orm import Orphans, Relation, map_aggregates
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.repository import Repository
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.template_table import entity_table
from sincpro_framework.data_layer.orm.sqlalchemy.infrastructure.database import Database
from sincpro_framework.ddd import (
    Derivations,
    Derive,
    EntityReads,
    Preview,
    ResponsePreview,
    Specification,
    assign,
)
from sincpro_framework.ddd.entity import Entity


@dataclass
class OrderLine(Entity):
    order_id: str
    product: str = ""
    qty: int = 1
    price: Decimal = Decimal("0")


@dataclass
class Order(Entity):
    customer_id: str
    lines: list[OrderLine] = field(default_factory=list)
    total: Decimal = Decimal("0")

    def total_of(self) -> Decimal:
        return sum((line.qty * line.price for line in self.lines), Decimal("0"))

    derivations = Derivations["Order"](
        lambda o: Derive(o.total, depends=o.lines, by=Order.total_of)
    )

    @classmethod
    def DEFAULT_READING(cls) -> Specification:
        return Specification.model_validate({"customer_id": {}, "lines": {}})


orders = registry(metadata=MetaData())
order_table = entity_table(
    "lined_order",
    orders.metadata,
    Column("customer_id", Text, nullable=False),
    Column("total", Numeric(12, 2), nullable=False),
)
line_table = entity_table(
    "lined_order_line",
    orders.metadata,
    Column("order_id", Text, ForeignKey("lined_order.id"), nullable=False),
    Column("product", Text, nullable=False),
    Column("qty", Integer, nullable=False),
    Column("price", Numeric(12, 2), nullable=False),
)
map_aggregates(
    orders,
    {Order: order_table, OrderLine: line_table},
    relations={
        Order: {
            "lines": Relation.foreign_key(
                OrderLine, identified_by="order_id", orphans=Orphans.DELETE
            )
        }
    },
)


class ResponsePreviewOrder(ResponsePreview):
    pass


class QueryPreviewOrder(Preview[Order, ResponsePreviewOrder]):
    pass


TYPED = [
    {"product": "cuaderno", "qty": 2, "price": "10"},
    {"product": "lapiz", "qty": 3, "price": "1.5"},
]


@pytest.fixture
def store() -> Repository:
    database = Database("sqlite://")
    orders.metadata.create_all(database.engine)
    return Repository(database)


def screen(store: Repository) -> UseFramework:
    bus = UseFramework("lined-orders", log_after_execution=False)
    bus.add_dependency("repository", store)

    @bus.feature(QueryPreviewOrder)
    class OrderReads(EntityReads[Order]):
        pass

    return bus


def test_a_new_order_is_previewed_with_lines_that_do_not_know_its_key(store):
    answer = screen(store)(
        QueryPreviewOrder(values={"customer_id": "c1", "lines": TYPED}, changed=["lines"]),
        ResponsePreviewOrder,
    )

    assert answer.values == {"total": "24.5"}


def test_a_new_order_is_created_with_its_lines_tied_to_it(store):
    with store.context() as repository:
        order = Order(customer_id="c1")
        assign(order, {"lines": TYPED})
        repository.save(order)

    with store.context() as repository:
        stored = repository.get(Order, order.id)
        assert stored is not None
        assert stored.total == Decimal("24.50")
        assert sorted(line.product for line in stored.lines) == ["cuaderno", "lapiz"]
        assert {line.order_id for line in stored.lines} == {order.id}


def test_a_key_the_form_sends_back_from_an_earlier_preview_is_replaced(store):
    stale = [{**one, "order_id": "an-id-from-a-previous-preview"} for one in TYPED]
    with store.context() as repository:
        order = Order(customer_id="c1")
        assign(order, {"lines": stale})
        repository.save(order)

    with store.context() as repository:
        stored = repository.get(Order, order.id)
        assert stored is not None
        assert {line.order_id for line in stored.lines} == {order.id}


def test_an_edit_changes_one_line_drops_another_and_adds_a_new_one(store):
    with store.context() as repository:
        order = Order(customer_id="c1")
        assign(order, {"lines": TYPED})
        repository.save(order)
    with store.context() as repository:
        stored = repository.get(Order, order.id)
        assert stored is not None
        notebook = next(line for line in stored.lines if line.product == "cuaderno")
        assign(
            stored,
            {
                "lines": [
                    {"id": notebook.id, "qty": 5},
                    {"product": "regla", "qty": 1, "price": "4"},
                ]
            },
        )
        repository.save(stored)

    with store.context() as repository:
        edited = repository.get(Order, order.id)
        assert edited is not None
        assert sorted((line.product, line.qty) for line in edited.lines) == [
            ("cuaderno", 5),
            ("regla", 1),
        ]
        assert edited.total == Decimal("54.00")
        assert next(line for line in edited.lines if line.product == "cuaderno").id == (
            notebook.id
        )
