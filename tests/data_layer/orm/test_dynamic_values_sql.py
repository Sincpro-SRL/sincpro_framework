"""A filter with `today` or the user, answered by the database.

The store reads the dynamic values when the filter runs, so what reaches SQL is only literals:
the same filter asked on another day is another statement, and a user comes from the server's
context.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import Column, Date, DateTime, Integer, MetaData, Table, Text
from sqlalchemy.orm import registry

from sincpro_framework.context.entrypoint.facade import carrying
from sincpro_framework.data_layer.orm import map_aggregates
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.repository import Repository
from sincpro_framework.ddd import exceptions
from sincpro_framework.ddd.criteria import (
    Condition,
    Criteria,
    FromContext,
    FromField,
    Grain,
    Grouping,
    Level,
    Measure,
    Operator,
    Period,
    Periods,
    Today,
)

from .engines import fresh


@dataclass
class Bill:
    id: str
    owner: str
    due: date
    posted_at: datetime
    amount: int = 0
    allowed: int = 0


bills_registry = registry(metadata=MetaData())
bills_table = Table(
    "dynamic_bill",
    bills_registry.metadata,
    Column("id", Text, primary_key=True),
    Column("owner", Text, nullable=False),
    Column("due", Date, nullable=False),
    Column("posted_at", DateTime, nullable=False),
    Column("amount", Integer, nullable=False),
    Column("allowed", Integer, nullable=False),
)
map_aggregates(bills_registry, {Bill: bills_table})


@pytest.fixture
def bills(engine_url: str) -> Repository:
    store = Repository(fresh(engine_url, bills_registry.metadata))
    today = datetime.now(UTC).date()
    with store.context() as repository:
        for ago, owner in [(10, "ana"), (1, "ana"), (0, "ana"), (-3, "beto")]:
            at = datetime.combine(today - timedelta(days=ago), datetime.min.time())
            repository.save(
                Bill(
                    id=f"B{ago}",
                    owner=owner,
                    due=today - timedelta(days=ago),
                    posted_at=at,
                    amount=100 + ago,
                    allowed=100,
                )
            )
    return store


def ids(found) -> list[str]:
    return sorted(one.id for one in found)


def test_the_database_runs_today_as_the_day_it_is_asked(bills: Repository):
    asked = Criteria(where=Condition(field="due", operator=Operator.LT, value=Today()))

    assert ids(bills.search(Bill, asked)) == ["B1", "B10"]


def test_thirty_days_ago_is_a_bound_of_the_statement(bills: Repository):
    since = Condition(field="due", operator=Operator.GTE, value=Today().before(days=5))

    assert ids(bills.search(Bill, Criteria(where=since))) == ["B-3", "B0", "B1"]


def test_an_instant_column_compares_to_a_date_as_its_midnight(bills: Repository):
    since = Condition(field="posted_at", operator=Operator.GTE, value=Today().before(days=1))

    assert ids(bills.search(Bill, Criteria(where=since))) == ["B-3", "B0", "B1"]


def test_a_period_is_two_bounds_and_the_end_is_not_included(bills: Repository):
    today = datetime.now(UTC).date()
    this_month = Condition(
        field="due", operator=Operator.BETWEEN, value=Period(name=Periods.THIS_MONTH)
    )

    found = ids(bills.search(Bill, Criteria(where=this_month)))

    expected = [
        f"B{ago}"
        for ago in (10, 1, 0, -3)
        if (today - timedelta(days=ago)).replace(day=1) == today.replace(day=1)
    ]
    assert found == sorted(expected)


def test_the_user_is_the_one_of_the_servers_context(bills: Repository):
    mine = Criteria(where=Condition(field="owner", value=FromContext(key="user_id")))

    with carrying({"user_id": "beto"}):
        assert ids(bills.search(Bill, mine)) == ["B-3"]
    with carrying({"user_id": "ana"}):
        assert ids(bills.search(Bill, mine)) == ["B0", "B1", "B10"]


def test_a_missing_context_key_refuses_the_search_and_does_not_widen_it(bills: Repository):
    mine = Criteria(where=Condition(field="owner", value=FromContext(key="user_id")))

    with carrying({}), pytest.raises(
        exceptions.InvalidCriteria, match="the context has no 'user_id'"
    ):
        bills.search(Bill, mine)


def test_a_column_is_compared_with_another_column_in_the_database(bills: Repository):
    over = Condition(field="amount", operator=Operator.GT, value=FromField(name="allowed"))

    assert ids(bills.search(Bill, Criteria(where=over))) == ["B1", "B10"]


def test_a_window_of_days_is_two_bounds_of_the_statement(bills: Repository):
    last = Condition(
        field="due", operator=Operator.BETWEEN, value=Period(unit=Grain.DAY, last=5)
    )

    assert ids(bills.search(Bill, Criteria(where=last))) == ["B1"]


def test_a_group_filter_may_read_the_servers_context_too(bills: Repository):
    """`where_measures` filters the groups the rows made, and its value is read like any other."""
    heavy = Grouping(
        group_by=(Level(field="owner"),),
        measures={"total": Measure(function="sum", field="amount")},
        where_measures=Condition(
            field="total", operator=Operator.GT, value=FromContext(key="floor")
        ),
    )

    with carrying({"floor": 150}):
        buckets = bills.group_by_levels(Bill, Criteria(grouping=heavy))

    assert [one.value for one in buckets] == ["ana"]
