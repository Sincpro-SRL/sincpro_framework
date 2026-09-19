"""A table of groups crossed by groups, and what a criteria says it will do before it does it.

The pivot's margins are checked against the cells on a `sum`, where adding up happens to be
right, and against the database on an `avg`, where it is not — which is why they are folded in
SQL and never added up here.
"""

import pytest

from sincpro_framework.ddd.criteria import (
    Condition,
    Criteria,
    Level,
    Specification,
    parse_order,
)
from sincpro_framework.ddd.criteria.pagination import Pagination
from sincpro_framework.ddd.exceptions import InvalidCriteria
from sincpro_framework.orm.sqlalchemy.repository import Repository

from .models import ROW_COUNT, Things


def test_a_pivot_crosses_two_axes_and_folds_the_cells(store: Repository, things, queries_run):
    with queries_run() as statements:
        matrix = store.pivot(Things, rows=["owner"], columns=["size"], weight=("sum", "size"))

    assert len(statements) == 4  # the cells, both margins and the total, whatever the rows
    assert matrix.rows == [[one] for one in sorted({t.owner for t in things}, key=str)]
    assert matrix.columns == [[one] for one in sorted({t.size for t in things})]
    for owner in {t.owner for t in things}:
        for size in {t.size for t in things}:
            mine = [t for t in things if t.owner == owner and t.size == size]
            cell = matrix.cell([owner], [size])
            if not mine:
                assert cell is None
            else:
                assert cell is not None
                assert cell.count == len(mine)
                assert cell.measures["weight"] == sum(t.size for t in mine)


def test_the_margins_are_folded_in_the_database_and_not_added_up(store: Repository, things):
    matrix = store.pivot(Things, rows=["owner"], columns=["size"], middle=("avg", "size"))

    assert matrix.total.count == ROW_COUNT
    assert matrix.total.measures["middle"] == pytest.approx(
        sum(t.size for t in things) / len(things)
    )
    for row in matrix.row_margin:
        mine = [t for t in things if t.owner == row.row[0]]
        assert row.count == len(mine)
        # An average of the cells' averages would land somewhere else entirely.
        assert row.measures["middle"] == pytest.approx(sum(t.size for t in mine) / len(mine))


def test_a_pivot_honours_the_filter_it_was_given(store: Repository, things):
    asked = Criteria(where=Condition(field="size", value=0))

    matrix = store.pivot(
        Things, rows=["owner"], columns=["size"], criteria=asked, n=("count", "size")
    )

    assert matrix.columns == [[0]]
    assert matrix.total.count == len([t for t in things if t.size == 0])


def test_a_pivot_cuts_a_date_on_either_axis(store: Repository):
    matrix = store.pivot(
        Things,
        rows=["owner"],
        columns=[Level(field="made_at", grain="day")],
        n=("count", "size"),
    )

    assert matrix.columns and all(len(one) == 1 and one[0] for one in matrix.columns)
    assert matrix.columns == sorted(matrix.columns)


def test_a_pivot_needs_both_axes_and_a_fold_it_knows(store: Repository):
    with pytest.raises(InvalidCriteria, match="a field on each axis"):
        store.pivot(Things, rows=["owner"], columns=[], weight=("sum", "size"))
    with pytest.raises(InvalidCriteria, match="is not a measure"):
        store.pivot(Things, rows=["owner"], columns=["size"], evil=("pg_sleep", "size"))
    with pytest.raises(InvalidCriteria):
        store.pivot(Things, rows=["nope"], columns=["size"], weight=("sum", "size"))


# ── explain ───────────────────────────────────────────────────────────────────


def test_explain_says_what_will_run_without_running_it(store: Repository, queries_run):
    asked = Criteria(
        where=Condition(field="size", value=1),
        order=parse_order("-made_at"),
        pagination=Pagination(limit=10),
    )

    with queries_run() as statements:
        explained = store.explain(Things, asked)

    assert statements == []
    assert "SELECT" in explained.sql and "WHERE" in explained.sql
    assert explained.ordering == ["-made_at", "-thing_id"]
    assert explained.statements == 2  # the page and its count
    assert explained.relations == []


def test_explain_names_what_the_model_refused(store: Repository):
    explained = store.explain(
        Things,
        Criteria(
            where=Condition(field="legacy_flag", value=1),
            specification=Specification({"nope": Criteria()}),
        ),
    )

    assert {(one.field, one.reason) for one in explained.dropped} == {
        ("legacy_flag", "unknown_field"),
        ("nope", "unknown_field"),
    }
