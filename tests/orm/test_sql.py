"""Every operator, translated and run against a real database.

This is the file where a mistake is silent: a wrong translation does not raise, it answers the
wrong rows. So each operator is asserted against the set it should match — computed from the
fixture's own rule rather than hardcoded, so the expectation cannot drift with the fixture.

The connectives get the same treatment, because `and_`/`or_`/`not_` over a pruned tree is where
an empty branch could quietly widen or empty a result.
"""

from datetime import datetime

import pytest

from sincpro_framework.ddd.criteria import (
    All,
    Any_,
    Condition,
    Criteria,
    Level,
    Not,
    Operator,
)
from sincpro_framework.ddd.pagination import Pagination
from sincpro_framework.orm.sqlalchemy.sql_translator import grouping_column

from .models import ROW_COUNT, Thing, Things, a_thing

EVERYTHING = ROW_COUNT + 10


def matching(store, expression) -> set[str]:
    """The ids the database answers for this expression."""
    return set(
        store.search(
            Things, Criteria(where=expression, pagination=Pagination(limit=EVERYTHING))
        ).ids
    )


def expected(predicate) -> set[str]:
    """The ids the fixture's own rule says should match."""
    return {a_thing(n).thing_id for n in range(ROW_COUNT) if predicate(a_thing(n))}


@pytest.mark.parametrize(
    "operator, value, rule",
    [
        (Operator.EQ, 0, lambda thing: thing.size == 0),
        (Operator.NE, 0, lambda thing: thing.size != 0),
        (Operator.GT, 3, lambda thing: thing.size > 3),
        (Operator.GTE, 3, lambda thing: thing.size >= 3),
        (Operator.LT, 1, lambda thing: thing.size < 1),
        (Operator.LTE, 1, lambda thing: thing.size <= 1),
        (Operator.IN, [0, 1], lambda thing: thing.size in (0, 1)),
        (Operator.NOT_IN, [0, 1], lambda thing: thing.size not in (0, 1)),
        # Inclusive at both ends: what "between 1 and 3" means to a person, and what
        # `BETWEEN` already means in SQL.
        (Operator.BETWEEN, [1, 3], lambda thing: 1 <= thing.size <= 3),
    ],
)
def test_the_operators_over_a_number(store, operator, value, rule):
    assert matching(
        store, Condition(field="size", operator=operator, value=value)
    ) == expected(rule)


def test_like_is_case_insensitive_containment(store):
    found = matching(store, Condition(field="name", operator=Operator.LIKE, value="THING 1"))

    assert found == expected(lambda thing: "thing 1" in thing.name.lower())


def test_is_null_answers_both_ways(store):
    absent = Condition(field="owner", operator=Operator.IS_NULL, value=True)
    present = Condition(field="owner", operator=Operator.IS_NULL, value=False)

    assert matching(store, absent) == expected(lambda thing: thing.owner is None)
    assert matching(store, present) == expected(lambda thing: thing.owner is not None)


def test_contains_and_its_negation_over_a_list_column(store):
    holds = Condition(field="tags", operator=Operator.CONTAINS, value="even")
    lacks = Condition(field="tags", operator=Operator.NOT_CONTAINS, value="even")

    assert matching(store, holds) == expected(lambda thing: "even" in thing.tags)
    assert matching(store, lacks) == expected(lambda thing: "even" not in thing.tags)


def test_contains_matches_a_whole_member_and_not_a_fragment(store):
    """Context: membership is a substring match over the stored JSON here, so a fragment of a
    member must not match — `n1` would otherwise find `n10` through `n19`."""
    found = matching(store, Condition(field="tags", operator=Operator.CONTAINS, value="n1"))

    assert found == {"th_0001"}


def test_a_conjunction_narrows(store):
    expression = All(
        all=[
            Condition(field="size", operator=Operator.GT, value=1),
            Condition(field="owner", operator=Operator.IS_NULL, value=False),
        ]
    )

    assert matching(store, expression) == expected(
        lambda thing: thing.size > 1 and thing.owner is not None
    )


def test_a_disjunction_widens(store):
    expression = Any_(
        any=[
            Condition(field="size", operator=Operator.EQ, value=0),
            Condition(field="size", operator=Operator.EQ, value=4),
        ]
    )

    assert matching(store, expression) == expected(lambda thing: thing.size in (0, 4))


def test_a_negation_inverts(store):
    expression = Not(negate=Condition(field="size", operator=Operator.EQ, value=0))

    assert matching(store, expression) == expected(lambda thing: thing.size != 0)


def test_nesting_holds_its_shape(store):
    """One `or` inside an `and`: the case where a flattened tree would answer differently."""
    expression = All(
        all=[
            Any_(
                any=[
                    Condition(field="size", operator=Operator.EQ, value=0),
                    Condition(field="size", operator=Operator.EQ, value=4),
                ]
            ),
            Condition(field="owner", operator=Operator.IS_NULL, value=True),
        ]
    )

    assert matching(store, expression) == expected(
        lambda thing: thing.size in (0, 4) and thing.owner is None
    )


def test_no_expression_matches_everything(store):
    assert matching(store, None) == expected(lambda thing: True)


def test_an_empty_list_column_matches_however_it_was_stored(store):
    """Context: a list column holds NULL for "never recorded" and `[]` for "recorded as
    nothing", and the reader gives back an empty list for both. A filter that matched only one
    of them would contradict what the reader answers — in the catalogue that meant the
    «uploaded» filter finding 1 dataset out of 185.
    """
    from sqlalchemy import text

    with store.database.session() as session:
        session.execute(
            text(
                "INSERT INTO thing (thing_id, name, size, tags, made_at, owner) "
                "VALUES ('th_null', 'stored as null', 0, NULL, '2026-01-01T00:00:00', NULL)"
            )
        )
        session.execute(
            text(
                "INSERT INTO thing (thing_id, name, size, tags, made_at, owner) "
                "VALUES ('th_empty', 'stored as empty', 0, '[]', '2026-01-01T00:00:00', NULL)"
            )
        )

    try:
        empty = matching(store, Condition(field="tags", operator=Operator.EQ, value=[]))
        filled = matching(store, Condition(field="tags", operator=Operator.NE, value=[]))

        assert empty == {"th_null", "th_empty"}
        assert "th_null" not in filled and "th_empty" not in filled
        assert len(filled) == ROW_COUNT
    finally:
        with store.database.session() as session:
            session.execute(
                text("DELETE FROM thing WHERE thing_id IN ('th_null', 'th_empty')")
            )


def test_a_range_asks_for_both_of_its_bounds(store):
    """Asking "between" with one bound is not half a question: the model drops it like any
    value that does not fit, and reports it."""
    page = store.search(
        Things,
        Criteria(where=Condition(field="size", operator=Operator.BETWEEN, value=[1])),
    )

    assert [one.reason for one in page.dropped] == ["bad_value"]


def test_a_date_grain_is_translated_for_postgres_as_well():
    """Context: the one non-portable spot in the translator. SQLite has `strftime`, Postgres
    `to_char`, and a `CAST` in between so a timestamp stored as ISO text groups the same."""
    from sqlalchemy.dialects import postgresql

    from sincpro_framework.ddd.criteria import Level
    from sincpro_framework.orm.sqlalchemy.sql_translator import grouping_column

    from .models import Thing

    monthly = grouping_column(Thing, Level(field="made_at", grain="month"), "postgresql")
    rendered = str(
        monthly.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )

    assert "to_char" in rendered and "YYYY-MM" in rendered and "TIMESTAMP" in rendered


def test_a_dialect_with_no_grain_translation_is_refused_rather_than_guessed():
    from sincpro_framework.ddd.criteria import Level
    from sincpro_framework.ddd.exceptions import ContractViolation
    from sincpro_framework.orm.sqlalchemy.sql_translator import grouping_column

    from .models import Thing

    with pytest.raises(ContractViolation, match="register_grain_translator"):
        grouping_column(Thing, Level(field="made_at", grain="month"), "oracle")


def test_the_week_grain_reads_the_same_on_both_dialects():
    """ISO weeks on SQLite and Postgres alike, so a bucket opened on one engine is the same
    bucket on the other; and the range a week bucket opens is its Monday to the next."""
    from sqlalchemy.dialects import postgresql, sqlite

    from sincpro_framework.orm.sqlalchemy.sql_translator import bucket_range

    weekly_sqlite = grouping_column(Thing, Level(field="made_at", grain="week"), "sqlite")
    weekly_pg = grouping_column(Thing, Level(field="made_at", grain="week"), "postgresql")

    assert "%G-W%V" in str(
        weekly_sqlite.compile(
            dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert 'IYYY-"W"IW' in str(
        weekly_pg.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert bucket_range("week", "2026-W01") == (datetime(2025, 12, 29), datetime(2026, 1, 5))
