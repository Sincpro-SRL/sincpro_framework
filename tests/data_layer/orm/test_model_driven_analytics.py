"""Define the model, get the analysis: a computed field, the part of a date, the groups with no
rows, and the folds a closed list adds — answered the same by a store in memory and by SQL.

Every case here runs twice, over the same rows. A memory store is what a project tests against
and SQL is what it runs on, so a number that differs between them is a bug in one of them and not
a difference of taste.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

import pytest
from sqlalchemy import Boolean, Column, DateTime, MetaData, Numeric, Text, select
from sqlalchemy.orm import registry

from sincpro_framework.data_layer.orm import map_aggregates
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.repository import Repository
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.template_table.entity import (
    entity_table,
)
from sincpro_framework.data_layer.orm.sqlalchemy.services.model_introspection import describe
from sincpro_framework.data_layer.repositories import MemoryRepository
from sincpro_framework.ddd import Entity, computed
from sincpro_framework.ddd.criteria import (
    All,
    Condition,
    Criteria,
    Grain,
    Grouping,
    Level,
    Measure,
    Operator,
    Pagination,
    Part,
    Sort,
)
from sincpro_framework.ddd.entity.entity_meta import describe_class
from sincpro_framework.ddd.exceptions import InvalidCriteria
from sincpro_framework.exceptions import ProgrammingError
from sincpro_framework.runtime.testing.contracts.computed_fields import (
    ComputedFieldsContract,
)

from .engines import ENGINES, fresh


class State(StrEnum):
    OPEN = "open"
    PAID = "paid"
    OVERDUE = "overdue"


@dataclass
class Bill(Entity):
    owner: str = ""
    state: State = State.OPEN
    total: Decimal = Decimal("0")
    paid: Decimal = Decimal("0")
    posted_at: datetime = datetime(2026, 1, 1)
    settled: bool = False
    balance: Decimal = computed(
        sql=lambda t: t.total - t.paid,
        python=lambda self: self.total - self.paid,
        default=Decimal("0"),
    )


@dataclass
class SqlOnly(Entity):
    total: int = 0
    doubled: int = computed(sql=lambda t: t.total * 2, default=0)


@dataclass
class Broken(Entity):
    total: int = 0
    nonsense: int = computed(sql=lambda t: t.nope + 1, default=0)


analytics = registry(metadata=MetaData())
bill_table = entity_table(
    "bill",
    analytics.metadata,
    Column("owner", Text),
    Column("state", Text),
    Column("total", Numeric(12, 2)),
    Column("paid", Numeric(12, 2)),
    Column("posted_at", DateTime),
    Column("settled", Boolean),
)
sql_only_table = entity_table("sql_only", analytics.metadata, Column("total", Text))
broken_table = entity_table("broken", analytics.metadata, Column("total", Text))
map_aggregates(analytics, {Bill: bill_table})

ROWS = [
    ("ana", State.OPEN, 100, 40, datetime(2026, 1, 5, 9), False),
    ("ana", State.PAID, 50, 50, datetime(2026, 1, 20, 9), True),
    ("beto", State.OPEN, 10, 0, datetime(2026, 3, 14, 15), False),
    ("beto", State.PAID, 200, 200, datetime(2027, 3, 2, 15), True),
    ("caro", State.OPEN, 70, 20, datetime(2026, 8, 31, 23), False),
]


def bill(row: tuple) -> Bill:
    owner, state, total, paid, at, settled = row
    return Bill(
        owner=owner,
        state=state,
        total=Decimal(total),
        paid=Decimal(paid),
        posted_at=at,
        settled=settled,
    )


@pytest.fixture(params=["memory", *ENGINES])
def bills(request: pytest.FixtureRequest) -> Any:
    """The same rows in a memory store and in every engine the suite has: SQLite always, and
    PostgreSQL when `SINCPRO_POSTGRES_URL` is given."""
    if request.param == "memory":
        store = MemoryRepository()
        for row in ROWS:
            store.save(bill(row))
        return store
    store = Repository(fresh(request.param, analytics.metadata))
    with store.context() as repository:
        for row in ROWS:
            repository.save(bill(row))
    return store


def grouped(
    store: Any, *levels: Level, measures: dict | None = None, where: Any = None
) -> Any:
    return store.group_by_levels(
        Bill,
        Criteria(
            where=where,
            grouping=Grouping(group_by=tuple(levels), measures=measures or {}),
        ),
    )


def counts(buckets: Any) -> dict:
    return {one.value: one.count for one in buckets}


# ── the model says what it can do ─────────────────────────────────────────────


def test_a_computed_field_says_so_and_is_read_only():
    for meta in (describe_class(Bill), describe(Bill)):
        field = meta.fields["balance"]

        assert (field.computed, field.readonly, field.groupable) == (True, True, True)
        assert meta.fields["total"].computed is False


def test_a_field_says_what_it_can_be_grouped_and_folded_by():
    fields = describe_class(Bill).fields

    assert fields["owner"].groupable and fields["owner"].aggregates == (
        "min",
        "max",
        "count",
        "count_distinct",
    )
    assert fields["total"].aggregates[-1] == "median"
    assert fields["settled"].aggregates == ("bool_and", "bool_or", "count")
    assert fields["posted_at"].aggregates == ("min", "max", "count", "count_distinct")


def test_a_computed_field_is_not_an_argument_of_the_constructor():
    assert SqlOnly(total=2).doubled == 0  # not read from a store: the default it declared
    with pytest.raises(TypeError):
        Bill(balance=Decimal("1"))  # type: ignore[call-arg]


# ── a computed field, in memory and in SQL ────────────────────────────────────


def test_a_computed_field_is_filtered_like_a_column(bills: Any):
    owing = Condition(field="balance", operator=Operator.GT, value=0)

    found = bills.search(Bill, Criteria(where=owing))

    assert sorted(one.owner for one in found) == ["ana", "beto", "caro"]
    assert sorted(one.balance for one in found) == [
        Decimal("10"),
        Decimal("50"),
        Decimal("60"),
    ]


def test_a_computed_field_is_ordered_like_a_column(bills: Any):
    found = bills.search(Bill, Criteria(order=(Sort(field="balance", descending=True),)))

    assert [one.balance for one in found] == [Decimal(v) for v in (60, 50, 10, 0, 0)]


def test_a_computed_field_is_grouped_and_folded_like_a_column(bills: Any):
    buckets = grouped(
        bills,
        Level(field="owner"),
        measures={"owing": Measure(function="sum", field="balance")},
    )

    assert {one.value: one.measures["owing"] for one in buckets} == {
        "ana": Decimal("60"),
        "beto": Decimal("10"),
        "caro": Decimal("50"),
    }
    assert counts(grouped(bills, Level(field="balance"))) == {
        Decimal("0"): 2,
        Decimal("10"): 1,
        Decimal("50"): 1,
        Decimal("60"): 1,
    }


def test_a_loaded_record_carries_the_value_the_store_computed(bills: Any):
    ana = bills.search(Bill, Criteria(where=Condition(field="owner", value="caro")))

    assert [one.balance for one in ana] == [Decimal("50")]


class TestBalanceAgreesInSql(ComputedFieldsContract):
    entity = Bill

    def make_store(self) -> Any:
        store = Repository(fresh("sqlite://", analytics.metadata))
        with store.context() as repository:
            for row in ROWS:
                repository.save(bill(row))
        return store


def test_a_computed_field_is_never_written():
    store = Repository(fresh("sqlite://", analytics.metadata))
    with store.context() as repository:
        repository.save(bill(ROWS[0]))

    with store.database.engine.connect() as connection:
        columns = connection.execute(select(bill_table)).keys()

    assert "balance" not in set(columns)


def test_a_computed_field_over_a_column_the_table_lacks_is_refused_when_mapped():
    with pytest.raises(ProgrammingError, match="Broken.nonsense.*does not have"):
        map_aggregates(registry(metadata=MetaData()), {Broken: broken_table})


def test_a_memory_store_refuses_a_field_it_has_no_python_for():
    store = MemoryRepository(SqlOnly(total=2))

    for criteria in (
        Criteria(where=Condition(field="doubled", operator=Operator.GT, value=1)),
        Criteria(order=(Sort(field="doubled"),)),
        Criteria(grouping=Grouping(group_by=(Level(field="doubled"),))),
    ):
        with pytest.raises(InvalidCriteria, match="SqlOnly.doubled is computed in SQL"):
            (
                store.search(SqlOnly, criteria)
                if not criteria.grouping.asked
                else (store.group_by_levels(SqlOnly, criteria))
            )


# ── the part of a date ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("part", "expected"),
    [
        (Part.YEAR, {2026: 4, 2027: 1}),
        (Part.QUARTER, {1: 4, 3: 1}),
        (Part.MONTH, {1: 2, 3: 2, 8: 1}),
        (Part.DAY_OF_WEEK, {1: 2, 2: 2, 6: 1}),
        (Part.DAY_OF_MONTH, {5: 1, 20: 1, 14: 1, 2: 1, 31: 1}),
        (Part.HOUR, {9: 2, 15: 2, 23: 1}),
    ],
)
def test_a_date_is_grouped_by_the_number_it_holds(bills: Any, part, expected):
    got = counts(grouped(bills, Level(field="posted_at", part=part)))

    assert got == {key: value for key, value in expected.items() if value}


def test_a_part_is_a_number_whatever_the_year(bills: Any):
    march = counts(grouped(bills, Level(field="posted_at", part=Part.MONTH)))[3]

    assert march == 2  # 2026-03-14 and 2027-03-02 are the same March


def test_the_weeks_are_iso_and_monday_is_one(bills: Any):
    weeks = counts(grouped(bills, Level(field="posted_at", part=Part.ISO_WEEK)))
    days = counts(grouped(bills, Level(field="posted_at", part=Part.DAY_OF_WEEK)))

    assert weeks[2] == 1  # 2026-01-05 is a Monday, the first day of ISO week 2
    assert days[1] == 2  # 2026-01-05 and 2026-08-31 are both Mondays
    assert 7 not in days


def test_a_part_cannot_be_opened_and_says_so(bills: Any):
    buckets = grouped(bills, Level(field="posted_at", part=Part.MONTH))

    assert all(one.openable is False for one in buckets)
    assert all(one.criteria.where is None for one in buckets)


def test_an_ordinary_group_can_be_opened(bills: Any):
    buckets = grouped(bills, Level(field="owner"))

    assert all(one.openable for one in buckets)


def test_a_level_takes_a_grain_or_a_part_and_not_both():
    with pytest.raises(InvalidCriteria, match="a grain.*or a part"):
        Level(field="posted_at", grain=Grain.MONTH, part=Part.MONTH)
    with pytest.raises(InvalidCriteria, match="not a part of a date"):
        Level(field="posted_at", part="fortnight")  # type: ignore[arg-type]


def test_a_page_of_ids_per_group_is_refused_for_a_part(bills: Any):
    asked = Criteria(
        pagination=Pagination(limit=2),
        grouping=Grouping(group_by=(Level(field="posted_at", part=Part.MONTH),)),
    )

    with pytest.raises(InvalidCriteria, match="has no rows to page"):
        bills.group_by_levels(Bill, asked)


# ── the groups with no rows ───────────────────────────────────────────────────


def test_an_enum_lists_every_choice_even_the_ones_with_no_rows(bills: Any):
    got = counts(grouped(bills, Level(field="state", fill=True)))

    assert got == {"open": 3, "overdue": 0, "paid": 2}


def test_a_boolean_lists_both_values(bills: Any):
    only_open = Condition(field="state", value="open")

    got = counts(grouped(bills, Level(field="settled", fill=True), where=only_open))

    assert got == {False: 3, True: 0}


def test_a_part_lists_every_number_it_can_be(bills: Any):
    got = grouped(bills, Level(field="posted_at", part=Part.MONTH, fill=True))

    assert [one.value for one in got] == list(range(1, 13))
    assert [one.count for one in got] == [2, 0, 2, 0, 0, 0, 0, 1, 0, 0, 0, 0]


def test_an_empty_group_folds_to_nothing_and_a_count_to_zero(bills: Any):
    buckets = grouped(
        bills,
        Level(field="state", fill=True),
        measures={
            "owing": Measure(function="sum", field="balance"),
            "owners": Measure(function="count_distinct", field="owner"),
        },
    )

    empty = next(one for one in buckets if one.value == "overdue")

    assert empty.measures == {"owing": None, "owners": 0}


def test_a_filled_level_is_listed_under_every_parent(bills: Any):
    buckets = grouped(bills, Level(field="owner"), Level(field="state", fill=True))

    assert {one.value: [(g.value, g.count) for g in one.groups] for one in buckets} == {
        "ana": [("open", 1), ("overdue", 0), ("paid", 1)],
        "beto": [("open", 1), ("overdue", 0), ("paid", 1)],
        "caro": [("open", 1), ("overdue", 0), ("paid", 0)],
    }


def test_a_level_with_nothing_to_list_cannot_be_filled(bills: Any):
    with pytest.raises(InvalidCriteria, match="cannot be filled"):
        grouped(bills, Level(field="owner", fill=True))


def test_a_filled_level_cannot_be_filtered_or_paged_by_its_groups(bills: Any):
    having = Grouping(
        group_by=(Level(field="state", fill=True),),
        where_measures=Condition(field="count", operator=Operator.GT, value=0),
    )

    with pytest.raises(InvalidCriteria, match="is filled"):
        bills.group_by_levels(Bill, Criteria(grouping=having))


def test_a_date_grain_is_filled_between_the_bounds_the_filter_gives():
    store = Repository(fresh("sqlite://", analytics.metadata))
    with store.context() as repository:
        for row in ROWS:
            repository.save(bill(row))
    quarter = All(
        all=[
            Condition(field="posted_at", operator=Operator.GTE, value=datetime(2026, 1, 1)),
            Condition(field="posted_at", operator=Operator.LT, value=datetime(2026, 5, 1)),
        ]
    )

    got = counts(
        grouped(store, Level(field="posted_at", grain=Grain.MONTH, fill=True), where=quarter)
    )

    assert got == {"2026-01": 2, "2026-02": 0, "2026-03": 1, "2026-04": 0}


def test_a_date_grain_with_no_bounds_cannot_be_filled():
    store = Repository(fresh("sqlite://", analytics.metadata))

    with pytest.raises(InvalidCriteria, match="needs the filter to bound it at both ends"):
        grouped(store, Level(field="posted_at", grain=Grain.MONTH, fill=True))


# ── the folds a closed list adds ──────────────────────────────────────────────


def test_all_or_any_of_a_boolean(bills: Any):
    got = {
        one.value: one.measures
        for one in grouped(
            bills,
            Level(field="owner"),
            measures={
                "all_settled": Measure(function="bool_and", field="settled"),
                "any_settled": Measure(function="bool_or", field="settled"),
            },
        )
    }

    assert got == {
        "ana": {"all_settled": False, "any_settled": True},
        "beto": {"all_settled": False, "any_settled": True},
        "caro": {"all_settled": False, "any_settled": False},
    }


def test_a_boolean_fold_is_refused_for_anything_that_is_not_a_boolean(bills: Any):
    with pytest.raises(InvalidCriteria, match="folds a boolean"):
        grouped(
            bills,
            Level(field="owner"),
            measures={"x": Measure(function="bool_and", field="total")},
        )


def test_the_median_is_the_half_percentile_and_needs_no_argument():
    assert Measure(function="median", field="total").argument is None
    with pytest.raises(InvalidCriteria, match="takes no argument"):
        Measure(function="median", field="total", argument=0.5)


def test_the_median_is_answered_in_memory_and_refused_where_sql_has_no_percentile():
    memory = MemoryRepository(*(bill(row) for row in ROWS))
    median = {"mid": Measure(function="median", field="total")}

    assert grouped(memory, Level(field="owner"), measures=median)[0].measures == {"mid": 75.0}

    sqlite = Repository(fresh("sqlite://", analytics.metadata))
    with pytest.raises(InvalidCriteria, match="does not compute percentiles"):
        grouped(sqlite, Level(field="owner"), measures=median)


def test_numbers_are_ordered_as_numbers_in_memory_as_in_sql(bills: Any):
    ordered = [
        one.value for one in grouped(bills, Level(field="posted_at", part=Part.DAY_OF_MONTH))
    ]

    assert ordered == sorted(ordered)


# ── the dialects this suite has no server for are compiled ────────────────────


@pytest.mark.parametrize(
    ("dialect", "part", "expected"),
    [
        ("postgresql", Part.MONTH, "EXTRACT(month FROM CAST(bill.posted_at AS TIMESTAMP"),
        ("postgresql", Part.DAY_OF_WEEK, "isodow"),
        ("postgresql", Part.ISO_WEEK, "EXTRACT(week FROM"),
        ("duckdb", Part.QUARTER, "date_part('quarter'"),
        ("duckdb", Part.DAY_OF_WEEK, "date_part('isodow'"),
        ("sqlite", Part.DAY_OF_WEEK, "strftime('%w'"),
    ],
)
def test_a_part_is_written_in_the_words_of_each_dialect(dialect, part, expected):
    from sqlalchemy.dialects import postgresql, sqlite

    from sincpro_framework.data_layer.orm.sqlalchemy.services.sql_translator import (
        grouping_column,
    )

    column = grouping_column(Bill, Level(field="posted_at", part=part), dialect)
    engine_dialect = {"postgresql": postgresql.dialect(), "sqlite": sqlite.dialect()}.get(
        dialect, postgresql.dialect()
    )
    rendered = str(
        column.compile(dialect=engine_dialect, compile_kwargs={"literal_binds": True})
    )

    assert expected in rendered


def test_a_dialect_with_no_part_translation_is_refused_naming_how_to_add_one():
    from sincpro_framework.data_layer.orm.sqlalchemy.services.sql_translator import (
        grouping_column,
    )

    with pytest.raises(ProgrammingError, match="register_part_translator"):
        grouping_column(Bill, Level(field="posted_at", part=Part.MONTH), "oracle")


def test_the_boolean_folds_use_the_native_function_where_there_is_one():
    from sqlalchemy.dialects import postgresql

    from sincpro_framework.data_layer.orm.sqlalchemy.services.aggregation import (
        measure_columns,
    )

    folds = {"all": Measure(function="bool_and", field="settled")}

    on_postgres = measure_columns(Bill, describe(Bill), folds, "postgresql")[0]
    on_sqlite = measure_columns(Bill, describe(Bill), folds, "sqlite")[0]

    assert "bool_and(" in str(on_postgres.compile(dialect=postgresql.dialect()))
    assert "min(" in str(on_sqlite.compile(dialect=postgresql.dialect())).lower()


# ── what a computed field does around a save and a page ───────────────────────


def test_a_page_ordered_by_a_computed_field_continues_where_it_ended(bills: Any):
    ordered = Criteria(
        order=(Sort(field="balance", descending=True),), pagination=Pagination(limit=2)
    )

    first = bills.search(Bill, ordered)
    second = bills.search(Bill, ordered.resuming_from(first.cursor))

    assert [one.balance for one in first] == [Decimal("60"), Decimal("50")]
    assert [one.balance for one in second] == [Decimal("10"), Decimal("0")]


def test_a_record_read_with_its_computed_field_can_be_changed_and_saved_again():
    store = Repository(fresh("sqlite://", analytics.metadata))
    with store.context() as repository:
        repository.save(bill(ROWS[0]))
    with store.context() as repository:
        read = repository.search(Bill, Criteria()).first()
        assert read is not None and read.balance == Decimal("60")
        read.paid = Decimal("100")
        repository.save(read)

    again = store.search(Bill, Criteria()).first()

    assert again is not None and again.balance == Decimal("0")


# ── a SQLite that predates strftime's ISO formats ─────────────────────────────


def test_the_iso_week_and_weekday_use_nothing_newer_than_sqlite_has_always_had():
    """`%V`, `%G` and `%u` came with SQLite 3.46 (May 2024); a Python built on an older library
    answers NULL for them. The week is computed from the Thursday of its week instead."""
    from sqlalchemy.dialects import sqlite

    from sincpro_framework.data_layer.orm.sqlalchemy.services.sql_translator import (
        grouping_column,
    )

    for level in (
        Level(field="posted_at", part=Part.ISO_WEEK),
        Level(field="posted_at", part=Part.DAY_OF_WEEK),
        Level(field="posted_at", grain=Grain.WEEK),
    ):
        rendered = str(
            grouping_column(Bill, level, "sqlite").compile(
                dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True}
            )
        )

        assert not any(newer in rendered for newer in ("%V", "%G", "%u")), rendered


def test_the_iso_week_and_weekday_agree_with_python_on_every_day_of_fifty_years():
    from datetime import date, timedelta

    from sqlalchemy import Column, DateTime, MetaData, Table, create_engine, insert

    from sincpro_framework.data_layer.orm.sqlalchemy.services.sql_translator import (
        _sqlite_grain,
        _sqlite_part,
    )

    days = [date(1990, 1, 1) + timedelta(days=n) for n in range(18_628)]
    table = Table("days", MetaData(), Column("posted_at", DateTime))
    engine = create_engine("sqlite://")
    table.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            insert(table), [{"posted_at": datetime(d.year, d.month, d.day, 13)} for d in days]
        )
        weeks = connection.execute(
            select(
                table.c.posted_at,
                _sqlite_part(table.c.posted_at, "iso_week"),
                _sqlite_part(table.c.posted_at, "day_of_week"),
                _sqlite_grain(table.c.posted_at, "week"),
            )
        ).all()

    wrong = [
        row
        for row in weeks
        if (row[1], row[2], row[3])
        != (
            row[0].isocalendar()[1],
            row[0].isoweekday(),
            f"{row[0].isocalendar()[0]}-W{row[0].isocalendar()[1]:02d}",
        )
    ]

    assert wrong == []


def test_the_median_is_the_same_number_in_memory_and_where_the_engine_has_percentiles(
    bills: Any,
):
    if isinstance(bills, Repository) and bills.database.engine.dialect.name == "sqlite":
        pytest.skip("SQLite has no percentile: it refuses by name, which is tested above")

    got = {
        one.value: one.measures["mid"]
        for one in grouped(
            bills,
            Level(field="owner"),
            measures={"mid": Measure(function="median", field="total")},
        )
    }

    assert {owner: float(value) for owner, value in got.items()} == {
        "ana": 75.0,
        "beto": 105.0,
        "caro": 70.0,
    }
