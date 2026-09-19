"""The two rules a collection lives by, and what breaks without each.

**A derived collection carries no metadata.** Without this, a filtered page keeps the cursor of
the page it came from, and somebody asks for the next page of a set that no longer exists.

**A partial collection refuses to aggregate.** Without this, `sum_by` over twenty records out
of eight thousand returns the sum of twenty and every caller reads it as a total. That is what
an Odoo recordset and a Laravel collection both do, silently, and it is the failure the
handbook names as worse than no answer at all.

The set operations are pinned for order as much as for membership: a list that reorders itself
between two identical requests is one nobody can page through.
"""

from dataclasses import dataclass
from datetime import datetime

import pytest

from sincpro_framework.ddd.criteria import Criteria, Specification
from sincpro_framework.ddd.entity_collection import (
    Count,
    Dropped,
    EntityCollection,
    identity_of,
)
from sincpro_framework.ddd.exceptions import ContractViolation
from sincpro_framework.sincpro_abstractions import DataTransferObject


@dataclass
class Row:
    row_id: str
    size: int = 0


class Rows(EntityCollection[Row]):
    """A subclass with nothing in it, to check that derivation keeps the type."""


def rows(*ids: str) -> Rows:
    return Rows(items=tuple(Row(row_id=one) for one in ids))


def test_identity_is_the_first_declared_field():
    assert identity_of(Row(row_id="a", size=3)) == "a"


def test_a_complete_collection_aggregates():
    complete = Rows(items=(Row("a", 2), Row("b", 3)), count=Count(value=2, exact=True))

    assert complete.sum_by(lambda row: row.size) == 5
    assert complete.average_by(lambda row: row.size) == 2.5
    assert complete.max_by(lambda row: row.size) == Row("b", 3)


def test_a_collection_with_no_metadata_is_not_partial():
    """Nobody asked for a count, so nothing here claims to be a fragment of anything."""
    assert Rows(items=(Row("a", 2),)).sum_by(lambda row: row.size) == 2


def test_holding_a_cursor_makes_it_partial():
    page = Rows(items=(Row("a", 2),), cursor="eyJrIjpbXX0")

    assert page.is_partial
    with pytest.raises(ContractViolation, match="sum_by"):
        page.sum_by(lambda row: row.size)


def test_a_floor_count_makes_it_partial():
    page = Rows(items=(Row("a", 2),), count=Count(value=10_000, exact=False))

    assert page.is_partial
    with pytest.raises(ContractViolation, match="self.repository.measures"):
        page.average_by(lambda row: row.size)


def test_an_exact_count_larger_than_what_is_held_makes_it_partial():
    page = Rows(items=(Row("a", 2),), count=Count(value=412, exact=True))

    assert page.is_partial


def test_every_derived_collection_drops_the_metadata():
    page = Rows(
        items=(Row("a", 1), Row("b", 2)),
        cursor="eyJrIjpbXX0",
        count=Count(value=99, exact=True),
    )

    derived = [
        page.filtered(lambda row: True),
        page.sorted_by(lambda row: row.size),
        page.take(1),
        page.skip(1),
        page | rows("c"),
        page & page,
        page - rows("a"),
        page ^ rows("c"),
        *page.partition(lambda row: True),
        *page.grouped(lambda row: row.row_id).values(),
        *page.chunk(1),
    ]

    assert all(one.cursor is None and one.count is None for one in derived)


def test_derivation_keeps_the_subclass():
    assert type(rows("a", "b").filtered(lambda row: True)) is Rows


def test_union_keeps_the_left_order_and_drops_repeats():
    assert (rows("a", "b") | rows("b", "c")).ids == ["a", "b", "c"]


def test_intersection_keeps_the_left_order():
    assert (rows("c", "a", "b") & rows("b", "a")).ids == ["a", "b"]


def test_difference_and_symmetric_difference():
    assert (rows("a", "b", "c") - rows("b")).ids == ["a", "c"]
    assert (rows("a", "b") ^ rows("b", "c")).ids == ["a", "c"]


def test_an_empty_collection_is_falsy():
    assert not Rows()
    assert rows("a")


def test_ensure_one_says_how_many_there_actually_were():
    assert rows("a").ensure_one().row_id == "a"

    with pytest.raises(ContractViolation, match="found 2"):
        rows("a", "b").ensure_one()


def test_grouping_buckets_what_is_held():
    held = Rows(items=(Row("a", 1), Row("b", 1), Row("c", 2)))

    buckets = held.grouped(lambda row: row.size)

    assert {size: group.ids for size, group in buckets.items()} == {1: ["a", "b"], 2: ["c"]}


def test_counting_what_is_held_is_not_the_same_name_as_how_many_exist():
    page = Rows(items=(Row("a", 1), Row("b", 2)), count=Count(value=99, exact=True))

    assert page.count_where() == 2
    assert page.count_where(lambda row: row.size > 1) == 1
    assert page.count == Count(value=99, exact=True)


def test_identity_falls_back_for_shapes_that_are_not_dataclasses():
    """`Run` is a pydantic model, not a dataclass — the first declared field still wins."""

    class Pydantic(DataTransferObject):
        run_id: str
        stage: str

    assert identity_of(Pydantic(run_id="run_1", stage="fitted")) == "run_1"
    assert identity_of("a bare value") == "a bare value"


def test_a_collection_reads_by_position():
    assert rows("a", "b")[1].row_id == "b"


def test_first_and_last_over_something_and_over_nothing():
    held = rows("a", "b")

    assert held.first() == Row("a")
    assert held.last() == Row("b")
    assert Rows().first() is None
    assert Rows().last() is None


def test_the_plain_collection_holds_anything_and_says_so():
    assert EntityCollection.holds() is None
    assert Rows.holds() is Row


def test_mapping_by_field_name_and_by_selector():
    held = Rows(items=(Row("a", 2), Row("b", 3)))

    assert held.mapped("size") == [2, 3]
    assert held.mapped(lambda row: row.row_id.upper()) == ["A", "B"]


def test_the_smallest_and_the_largest_come_back_as_records():
    held = Rows(items=(Row("a", 2), Row("b", 3)))

    assert held.min_by(lambda row: row.size) == Row("a", 2)
    assert held.max_by(lambda row: row.size) == Row("b", 3)
    assert Rows().min_by(lambda row: row.size) is None
    assert Rows().max_by(lambda row: row.size) is None


def test_the_summary_says_what_it_is_holding_without_saying_what_is_in_it():
    page = Rows(
        items=(Row("a", 1),),
        cursor="token",
        count=Count(value=197, exact=False),
        dropped=(Dropped(field="gone", reason="unknown_field"),),
    )

    assert repr(page) == "Rows(1 records of 197+, more, 1 dropped)"
    assert "a" not in repr(page).replace("Rows", "")


# ── the lookups a use case does over a page it already has ───────────────────


@dataclass
class Thing:
    thing_id: str
    name: str
    size: int
    made_at: datetime


def a_thing(number: int) -> Thing:
    return Thing(
        thing_id=f"th_{number:04d}",
        name=f"thing {number}",
        size=number % 5,
        made_at=datetime(2026, 1, 1, 12, number),
    )


def test_records_are_indexed_by_a_value_of_their_own():
    page = EntityCollection(items=(a_thing(1), a_thing(2)))

    by_name = page.index_by("name")

    assert by_name["thing 1"].thing_id == "th_0001"
    assert page.index_by(lambda thing: thing.size)[a_thing(2).size].thing_id == "th_0002"


def test_records_become_dictionaries_whole_or_masked():
    page = EntityCollection(items=(a_thing(3),))

    whole = page.to_records()
    masked = page.to_records(Specification({"name": Criteria()}))

    assert set(whole[0]) == {"thing_id", "name", "size", "made_at"}
    assert masked == [{"thing_id": "th_0003", "name": "thing 3"}]
    assert isinstance(whole[0]["made_at"], datetime)  # Python values, not JSON


def test_a_mask_reaches_inside_what_a_record_holds():
    @dataclass
    class Line:
        line_id: str
        amount: int

    @dataclass
    class Order:
        order_id: str
        lines: list[Line]

    page = EntityCollection(
        items=(Order(order_id="o1", lines=[Line("l1", 5), Line("l2", 7)]),)
    )

    masked = page.to_records(
        Specification(
            {"lines": Criteria(specification=Specification({"amount": Criteria()}))}
        )
    )

    assert masked == [
        {
            "order_id": "o1",
            "lines": [{"line_id": "l1", "amount": 5}, {"line_id": "l2", "amount": 7}],
        }
    ]


def test_two_collections_differ_by_identity():
    stored = EntityCollection(items=(a_thing(1), a_thing(2), a_thing(3)))
    brought = EntityCollection(items=(a_thing(2), a_thing(3), a_thing(4)))

    changes = brought.changes_against(stored)

    assert [one.thing_id for one in changes.added] == ["th_0004"]
    assert [one.thing_id for one in changes.removed] == ["th_0001"]
    assert changes.changed == ()
    assert len(changes.unchanged) == 2
    assert changes.any is True


def test_what_changed_is_what_the_comparison_says_changed():
    stored = EntityCollection(items=(a_thing(1),))
    renamed = a_thing(1)
    renamed.name = "renamed"
    brought = EntityCollection(items=(renamed,))

    by_everything = brought.changes_against(stored)
    by_size = brought.changes_against(
        stored, same=lambda mine, theirs: mine.size == theirs.size
    )

    assert len(by_everything.changed) == 1
    assert by_everything.changed[0][0].name == "renamed"
    assert by_size.changed == () and len(by_size.unchanged) == 1


def test_a_collection_against_itself_has_nothing_to_say():
    page = EntityCollection(items=(a_thing(1), a_thing(2)))

    assert page.changes_against(page).any is False
