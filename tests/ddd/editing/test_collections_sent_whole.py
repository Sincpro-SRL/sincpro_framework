"""A list of records travels as the collection a reading answered — `{items, count, cursor}` —
and comes back the same way: whole, it is written as its records; a page of it is refused,
because written back it would remove what it did not bring."""

from dataclasses import dataclass, field

import pytest

from sincpro_framework.ddd import apply_values
from sincpro_framework.ddd import exceptions as ddd_exceptions
from sincpro_framework.ddd.editing.editing import form_values
from sincpro_framework.ddd.entity import Entity


@dataclass
class Line(Entity):
    product: str = ""
    qty: int = 0


@dataclass
class Order(Entity):
    number: str = ""
    lines: list[Line] = field(default_factory=list)


def test_a_whole_collection_is_written_as_its_records():
    order = Order(number="O-1")

    apply_values(
        order,
        {
            "lines": {
                "items": [{"product": "pan", "qty": 2}, {"product": "leche", "qty": 1}],
                "count": {"value": 2, "exact": True},
                "cursor": None,
            }
        },
    )

    assert [(line.product, line.qty) for line in order.lines] == [("pan", 2), ("leche", 1)]


def test_a_collection_without_a_count_is_read_as_what_it_holds():
    order = Order(number="O-1")

    apply_values(order, {"lines": {"items": [{"product": "pan", "qty": 2}]}})

    assert [line.product for line in order.lines] == ["pan"]


def test_a_whole_collection_a_form_edited_is_written_though_its_count_is_the_one_it_read():
    order = Order(number="O-1")

    apply_values(
        order,
        {
            "lines": {
                "items": [{"product": "pan", "qty": 2}],
                "count": {"value": 2, "exact": True},
            }
        },
    )

    assert [line.product for line in order.lines] == ["pan"]


@pytest.mark.parametrize(
    "page",
    [
        {"items": [{"product": "pan", "qty": 2}], "count": {"value": 1000, "exact": False}},
        {"items": [{"product": "pan", "qty": 2}], "count": None, "cursor": "eyJrIjpbXX0"},
    ],
    ids=["a floor", "a cursor"],
)
def test_a_page_of_it_is_refused_naming_the_field(page):
    order = Order(number="O-1", lines=[Line(product="harina", qty=1)])

    with pytest.raises(ddd_exceptions.InvalidValue) as refused:
        apply_values(order, {"lines": page})

    assert "Order.lines was sent as a page" in str(refused.value)
    assert [line.product for line in order.lines] == ["harina"]


def test_a_form_reads_back_the_collection_it_was_given():
    written = form_values(
        Order,
        {
            "number": "O-1",
            "lines": {
                "items": [{"id": "l1", "version": 3, "product": "pan", "qty": 2}],
                "count": {"value": 1, "exact": True},
                "cursor": None,
            },
        },
    )

    assert written["lines"]["items"] == [{"id": "l1", "product": "pan", "qty": 2}]
