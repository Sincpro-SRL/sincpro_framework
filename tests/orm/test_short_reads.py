"""The questions a use case asks a hundred times a day, each answered by the cheapest statement
that can answer it: is there one, give me the first, give me the only one, give me that column.

Every one of them is a `search` somebody would otherwise write with a wrapper around it, and
every one of them is where a page slips in by accident — which is why the ones that answer over
the whole result set say so.
"""

import pytest

from sincpro_framework.ddd.criteria import (
    Condition,
    Criteria,
    Operator,
    Specification,
    parse_order,
)
from sincpro_framework.ddd.criteria.pagination import Pagination
from sincpro_framework.ddd.exceptions import ContractViolation, InvalidCriteria
from sincpro_framework.orm.sqlalchemy.repository import Repository

from .models import ROW_COUNT, Thing, Things


def test_exists_answers_without_counting_or_fetching(store: Repository, queries_run):
    with queries_run() as statements:
        assert store.exists(Things, Criteria(where=Condition(field="size", value=3))) is True
        assert (
            store.exists(Things, Criteria(where=Condition(field="size", value=99))) is False
        )

    assert len(statements) == 2
    assert all("LIMIT" in one.upper() for one in statements)
    assert not any("count(" in one.lower() for one in statements)


def test_first_is_the_one_the_order_puts_in_front(store: Repository, things):
    biggest = store.first(Things, Criteria(order=parse_order("-size,-thing_id")))
    smallest = store.first(Things, Criteria(order=parse_order("size,thing_id")))

    assert biggest is not None and smallest is not None
    assert biggest.size == max(one.size for one in things)
    assert smallest.size == min(one.size for one in things)
    assert store.first(Things, Criteria(where=Condition(field="size", value=99))) is None


def test_one_refuses_none_and_refuses_two(store: Repository, things):
    only = store.one(Things, Criteria(where=Condition(field="thing_id", value="th_0007")))

    assert only.thing_id == "th_0007"
    with pytest.raises(ContractViolation):
        store.one(Things, Criteria(where=Condition(field="size", value=3)))
    with pytest.raises(ContractViolation):
        store.one(Things, Criteria(where=Condition(field="size", value=99)))


def test_one_never_loads_the_set_to_find_out_it_was_not_one(store: Repository, queries_run):
    with queries_run() as statements:
        with pytest.raises(ContractViolation):
            store.one(Things)

    assert any("LIMIT" in one.upper() for one in statements)


def test_get_by_answers_a_natural_key(store: Repository):
    found = store.get_by(Things, name="thing 4")

    assert found is not None and found.thing_id == "th_0004"
    assert store.get_by(Things, name="nobody") is None
    with pytest.raises(ContractViolation, match="a natural key names one"):
        store.get_by(Things, size=3)
    with pytest.raises(InvalidCriteria):
        store.get_by(Things)


def test_pluck_answers_one_column_over_the_whole_result_set(store: Repository, things):
    """Over everything the criteria matched, like `count` and `measures`: a column of values
    fills a select or seeds a browse, and a page of it would be an accident."""
    every = store.pluck(Things, "thing_id", Criteria(order=parse_order("thing_id")))

    assert len(every) == ROW_COUNT
    assert every == sorted(one.thing_id for one in things)
    assert store.browse(Things, every[:3]).ids == every[:3]


def test_pluck_honours_the_filter_and_refuses_an_unknown_column(store: Repository):
    only = store.pluck(Things, "name", Criteria(where=Condition(field="size", value=0)))

    assert only and all(name.startswith("thing ") for name in only)
    with pytest.raises(InvalidCriteria):
        store.pluck(Things, "legacy_flag")


def test_distinct_answers_what_a_column_actually_holds(store: Repository, things):
    sizes = store.distinct(Things, "size")
    owners = store.distinct(Things, "owner")

    assert sizes == sorted({one.size for one in things})
    assert None in owners and len(owners) == len({one.owner for one in things})


def test_export_walks_every_page_as_dictionaries(store: Repository, things):
    mask = Specification({"thing_id": Criteria(), "name": Criteria()})

    rows = list(
        store.export(Things, Criteria(pagination=Pagination(limit=4)), specification=mask)
    )

    assert len(rows) == ROW_COUNT
    assert all(set(row) == {"thing_id", "name"} for row in rows)
    assert {row["thing_id"] for row in rows} == {one.thing_id for one in things}


def test_export_without_a_mask_writes_every_field(store: Repository):
    row = next(store.export(Things, Criteria(pagination=Pagination(limit=2))))

    assert set(row) == {"thing_id", "name", "size", "tags", "made_at", "owner"}
    assert isinstance(row["made_at"], Thing.__dataclass_fields__["made_at"].type)


def test_export_takes_the_mask_from_the_criteria_when_it_carries_one(store: Repository):
    asked = Criteria(
        pagination=Pagination(limit=5), specification=Specification({"size": Criteria()})
    )

    rows = list(store.export(Things, asked))

    assert all(set(row) == {"thing_id", "size"} for row in rows)


def test_a_short_reading_asks_for_no_definition_it_will_not_use(
    store: Repository, queries_run
):
    """`first` and `one` answer a record, not a page: nothing counts and nothing describes."""
    with queries_run() as statements:
        store.first(Things)

    assert not any("count(" in one.lower() for one in statements)


@pytest.mark.parametrize("operator", [Operator.GT, Operator.LT])
def test_the_short_readings_speak_the_same_criteria_as_search(store: Repository, operator):
    asked = Criteria(where=Condition(field="size", value=2, operator=operator))

    assert store.exists(Things, asked) is (store.count(Things, asked).value > 0)
    assert store.first(Things, asked) is not None
    assert len(store.pluck(Things, "size", asked)) == store.count(Things, asked).value
