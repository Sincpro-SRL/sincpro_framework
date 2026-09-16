"""That the filter language means the same thing in memory as it does in SQL.

Two evaluators over one `Expression` drift apart the first time somebody fixes one and forgets
the other, so every operator is asked of both against the same rows and the two sets are
compared. The `NULL` cases are the ones worth having: SQL excludes a row whose column is NULL
from every comparison, and a Python evaluator that answered `None != 'x'` as true would keep it.
"""

import pytest

from sincpro_framework.ddd.criteria import All, Any_, Condition, Criteria, Not, Operator
from sincpro_framework.ddd.entity_collection import Count
from sincpro_framework.ddd.evaluate import matches
from sincpro_framework.ddd.exceptions import InvalidCriteria
from sincpro_framework.ddd.pagination import Pagination
from sincpro_framework.orm.sqlalchemy.model_introspection import describe

from .models import ROW_COUNT, Thing, Things, a_thing

EVERYTHING = ROW_COUNT + 10


def in_sql(store, expression) -> set[str]:
    return set(
        store.search(
            Things, Criteria(where=expression, pagination=Pagination(limit=EVERYTHING))
        ).ids
    )


def in_memory(things: list[Thing], expression) -> set[str]:
    """Through `Meta.accept` first, the way the engine does: that is what reads a date
    written as text into a date, so both evaluators compare the same values."""
    accepted, dropped = describe(Thing).accept(expression)
    assert dropped == []
    return {thing.thing_id for thing in things if matches(thing, accepted)}


@pytest.mark.parametrize(
    "expression",
    [
        Condition(field="size", operator=Operator.EQ, value=0),
        Condition(field="size", operator=Operator.NE, value=0),
        Condition(field="size", operator=Operator.GT, value=3),
        Condition(field="size", operator=Operator.GTE, value=3),
        Condition(field="size", operator=Operator.LT, value=1),
        Condition(field="size", operator=Operator.LTE, value=1),
        Condition(field="size", operator=Operator.IN, value=[0, 1]),
        Condition(field="size", operator=Operator.NOT_IN, value=[0, 1]),
        Condition(field="size", operator=Operator.BETWEEN, value=[1, 3]),
        Condition(field="name", operator=Operator.LIKE, value="THING 1"),
        Condition(field="tags", operator=Operator.CONTAINS, value="even"),
        Condition(field="tags", operator=Operator.NOT_CONTAINS, value="n1"),
        Condition(field="tags", operator=Operator.EQ, value=[]),
        Condition(field="tags", operator=Operator.NE, value=[]),
        Condition(field="owner", operator=Operator.IS_NULL, value=True),
        Condition(field="owner", operator=Operator.IS_NULL, value=False),
        # The rows holding NULL are excluded on both sides, which is the case a naive `!=`
        # gets wrong.
        Condition(field="owner", operator=Operator.NE, value="owner-1"),
        Condition(field="owner", operator=Operator.EQ, value="owner-1"),
        Condition(field="owner", operator=Operator.IN, value=["owner-1", "owner-2"]),
        Condition(field="owner", operator=Operator.NOT_IN, value=["owner-1"]),
        Condition(field="owner", operator=Operator.LIKE, value="OWNER"),
        Condition(field="made_at", operator=Operator.GT, value="2026-01-01T12:10:00"),
        Condition(
            field="made_at",
            operator=Operator.BETWEEN,
            value=["2026-01-01T12:03:00", "2026-01-01T12:07:00"],
        ),
        All(
            all=[
                Condition(field="size", operator=Operator.GTE, value=2),
                Not(negate=Condition(field="owner", operator=Operator.IS_NULL, value=True)),
            ]
        ),
        Any_(
            any=[
                Condition(field="size", operator=Operator.EQ, value=4),
                Condition(field="tags", operator=Operator.CONTAINS, value="n3"),
            ]
        ),
        None,
    ],
    ids=repr,
)
def test_both_evaluators_answer_the_same_rows(store, things, expression):
    assert in_memory(things, expression) == in_sql(store, expression)


def test_a_null_answers_nothing_but_the_null_question():
    orphan = a_thing(3)
    assert orphan.owner is None

    assert not matches(orphan, Condition(field="owner", operator=Operator.NE, value="x"))
    assert not matches(orphan, Condition(field="owner", operator=Operator.LIKE, value=""))
    assert matches(orphan, Condition(field="owner", operator=Operator.IS_NULL, value=True))


def test_an_operator_nobody_translated_is_refused():
    condition = Condition.model_construct(field="size", value=1, operator="nope")

    with pytest.raises(InvalidCriteria, match="no in-memory evaluation"):
        matches(a_thing(1), condition)


def test_a_page_narrows_itself_by_the_same_filter_a_caller_would_send(store):
    page = store.search(Things, Criteria(pagination=Pagination(limit=ROW_COUNT + 10)))

    kept = page.filtered_by(Condition(field="size", operator=Operator.EQ, value=0))

    assert type(kept) is Things
    assert kept.ids == [one.thing_id for one in page if one.size == 0]
    assert kept.cursor is None and kept.count is None
    assert kept.meta is page.meta
    assert page.filtered_by(None).ids == page.ids


def test_a_hand_built_collection_can_be_filtered_too(things):
    held = Things(items=tuple(things), count=Count(value=ROW_COUNT, exact=True))

    assert len(held.filtered_by(Condition(field="size", operator=Operator.GT, value=2))) == 10
