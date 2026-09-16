"""Reading a populated ledger: every page, every operator, every count, at whatever volume
the run was given.

The comparisons are against facts the population knows (`census`) and against the in-memory
evaluator over the whole set, so a translation that drifts from what `matches` says shows up
here as a set difference and not as a hunch.
"""

from datetime import datetime
from math import ceil

import pytest

from sincpro_framework.ddd.criteria import (
    All,
    Condition,
    CountMode,
    Criteria,
    Operator,
    parse_order,
)
from sincpro_framework.ddd.evaluate import matches
from sincpro_framework.ddd.pagination import Pagination
from sincpro_framework.orm.sqlalchemy.model_introspection import describe
from sincpro_framework.orm.sqlalchemy.repository import DEFAULT_COUNT_CAP, Repository

from .ledger import Account, Entries, Entry, Line, Lines
from .population import POPULATION, Census

PAGE = 500


@pytest.fixture(scope="module")
def every_line(database, census) -> Lines:
    """The whole set of lines in memory, loaded once for the module: what the SQL answers
    are compared against."""
    return Repository(database).fetch_all(
        Lines, Criteria(where=POPULATION, pagination=Pagination(limit=1000))
    )


def test_a_keyset_walk_visits_every_line_once_in_order(
    ledger: Repository, census: Census, timed
):
    criteria = Criteria(
        where=POPULATION, order=parse_order("-posted_at"), pagination=Pagination(limit=PAGE)
    )

    with timed("keyset walk, pages of 500", census.lines):
        pages = list(ledger.stream(Lines, criteria))

    visited = [line for page in pages for line in page]
    assert len(visited) == census.lines
    assert len({line.id for line in visited}) == census.lines
    moments = [line.posted_at for line in visited]
    assert moments == sorted(moments, reverse=True)
    assert len(pages) <= ceil(census.lines / PAGE) + 1


def test_the_capped_count_says_when_it_stopped_counting(ledger: Repository, census: Census):
    capped = ledger.count(Line, Criteria(where=POPULATION))
    exact = ledger.count(Line, Criteria(where=POPULATION, count=CountMode.EXACT))

    assert exact.value == census.lines and exact.exact
    if census.lines <= DEFAULT_COUNT_CAP:
        assert capped == exact
    else:
        assert capped.value == DEFAULT_COUNT_CAP and not capped.exact


@pytest.mark.parametrize(
    "expression",
    [
        Condition(field="debit", value=2500, operator=Operator.GT),
        Condition(field="credit", value=0, operator=Operator.EQ),
        Condition(field="partner_id", value=True, operator=Operator.IS_NULL),
        Condition(field="label", value="counterpart", operator=Operator.LIKE),
        Condition(
            field="posted_at",
            value=["2024-06-01T00:00:00", "2024-09-01T00:00:00"],
            operator=Operator.BETWEEN,
        ),
        All(
            all=[
                Condition(field="entry_state", value="posted"),
                Condition(field="debit", value=4000, operator=Operator.GTE),
                Condition(
                    field="posted_at", value="2025-01-01T00:00:00", operator=Operator.LT
                ),
            ]
        ),
    ],
    ids=["gt", "eq-zero", "is-null", "like", "between-dates", "all-of-three"],
)
def test_the_sql_answer_is_the_in_memory_answer(
    ledger: Repository, every_line: Lines, expression
):
    """`matches` is the specification; the translation has to agree with it on every row.
    The values are read through the definition first, the way the repository reads them, so
    the ISO strings above become datetimes on both sides."""
    read, dropped = describe(Line).accept(All(all=[POPULATION, expression]))
    assert not dropped

    fetched = ledger.fetch_all(Lines, Criteria(where=read, pagination=Pagination(limit=1000)))
    expected = {line.id for line in every_line if matches(line, read)}

    assert expected, "a filter that matches nothing proves nothing"
    assert set(fetched.ids) == expected
    assert len(fetched) < len(every_line)


def test_browse_answers_in_the_order_asked_and_drops_the_unknown(
    ledger: Repository, masters: dict[str, list]
):
    accounts = masters["accounts"]
    wanted = [accounts[7].id, accounts[2].id, "acc_nobody", accounts[30].id]

    found = ledger.browse(Account, wanted)

    assert found.ids == [accounts[7].id, accounts[2].id, accounts[30].id]


def test_set_algebra_over_two_readings_is_one_combined_reading(
    ledger: Repository, masters: dict[str, list]
):
    journal = masters["journals"][0]
    account = masters["accounts"][0]
    of_journal = ledger.fetch_all(
        Lines, Criteria(where=Condition(field="journal_id", value=journal.id))
    )
    of_account = ledger.fetch_all(
        Lines, Criteria(where=Condition(field="account_id", value=account.id))
    )
    combined = ledger.fetch_all(
        Lines,
        Criteria(
            where=All(
                all=[
                    Condition(field="journal_id", value=journal.id),
                    Condition(field="account_id", value=account.id),
                ]
            )
        ),
    )

    assert set((of_journal & of_account).ids) == set(combined.ids)
    assert set((of_journal | of_account).ids) == set(of_journal.ids) | set(of_account.ids)
    assert set((of_journal - of_account).ids) == set(of_journal.ids) - set(combined.ids)


def test_the_entries_of_one_month_page_by_fifty(ledger: Repository, census: Census):
    month = Criteria(
        where=Condition(
            field="posted_at",
            value=["2024-03-01T00:00:00", "2024-04-01T00:00:00"],
            operator=Operator.BETWEEN,
        ),
        order=parse_order("posted_at"),
        pagination=Pagination(limit=50),
        count=CountMode.EXACT,
    )

    first = ledger.search(Entries, month)
    walked = ledger.fetch_all(Entries, month)

    assert first.count is not None and first.count.exact
    assert len(walked) == first.count.value
    assert all(
        datetime(2024, 3, 1) <= entry.posted_at < datetime(2024, 4, 1) for entry in walked
    )
    assert 0 < len(walked) < census.entries


def test_get_answers_none_for_an_unknown_identity(ledger: Repository):
    assert ledger.get(Entry, "ent_nobody") is None
