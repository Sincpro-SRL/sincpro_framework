"""The same criteria, answered over a Parquet file.

**The point of this file is that nothing in it is special.** The reading is the same object a
screen sends to Postgres; what changes is the engine underneath and a view that happens to be
a file on disk. If a criteria only worked over a mapped table, the vocabulary would be a
database API with extra steps.

DuckDB is installed as the optional extra `duckdb`: `poetry install --extras duckdb`. Without
it the whole module is skipped, because a dependency nobody asked for is not allowed to break
the suite.

The rows are generated and not downloaded: a year of daily sales with a shape that repeats, so
every number below can be worked out by hand and the test says the same thing on a machine
with no network.
"""

from dataclasses import dataclass
from datetime import datetime

import pytest

pytest.importorskip("duckdb_engine", reason="the optional `duckdb` extra is not installed")

from sqlalchemy import Column, DateTime, Integer, Table, Text  # noqa: E402
from sqlalchemy.orm import registry  # noqa: E402

from sincpro_framework.ddd.criteria import Criteria  # noqa: E402
from sincpro_framework.ddd.criteria import Condition, Grouping, Level, Measure, Operator, Sort
from sincpro_framework.ddd.entity_collection import EntityCollection  # noqa: E402
from sincpro_framework.ddd.pagination import Pagination  # noqa: E402
from sincpro_framework.orm.sqlalchemy.data_mapper import map_aggregates  # noqa: E402
from sincpro_framework.orm.sqlalchemy.database import Database  # noqa: E402
from sincpro_framework.orm.sqlalchemy.repository import Repository  # noqa: E402

DAYS = 365
REGIONS = ("north", "south", "east")


@dataclass
class Sale:
    sale_id: str
    region: str
    rep: str
    amount: int
    at: datetime


class Sales(EntityCollection[Sale]):
    pass


parquet_registry = registry()

sale_table = Table(
    "sale",
    parquet_registry.metadata,
    Column("sale_id", Text, primary_key=True),
    Column("region", Text, nullable=False),
    Column("rep", Text, nullable=False),
    Column("amount", Integer, nullable=False),
    Column("at", DateTime, nullable=False),
)

map_aggregates(parquet_registry, {Sale: sale_table})


@pytest.fixture(scope="module")
def parquet(tmp_path_factory) -> str:
    """A year of daily sales, written to a Parquet file by DuckDB itself.

    Deterministic on purpose: the amount cycles with the day, so a month's total is a number
    this test can state rather than a number it reads back from what it just wrote.
    """
    import duckdb  # pyright: ignore[reportMissingImports]

    path = tmp_path_factory.mktemp("parquet") / "sales.parquet"
    duckdb.connect().execute(f"""
        COPY (
            SELECT
                'sale_' || i                                   AS sale_id,
                ['{REGIONS[0]}', '{REGIONS[1]}', '{REGIONS[2]}'][(i % 3) + 1] AS region,
                ['ana', 'beto', 'cyn', 'dita'][(i % 4) + 1]    AS rep,
                (100 + (i * 37) % 900)::INTEGER                AS amount,
                (TIMESTAMP '2026-01-01 00:00:00' + INTERVAL (i) DAY) AS at
            FROM range(0, {DAYS}) t(i)
        ) TO '{path}' (FORMAT PARQUET)
        """)
    return str(path)


@pytest.fixture(scope="module")
def store(parquet: str) -> Repository:
    """A repository over a view whose table is a file.

    The view is the whole adapter: from here down, nothing knows the rows are not in a table.
    """
    database = Database(f"duckdb:///{':memory:'}")
    with database.engine.begin() as connection:
        from sqlalchemy import text

        connection.execute(text(f"CREATE VIEW sale AS SELECT * FROM '{parquet}'"))
    return Repository(database)


def test_a_page_of_a_parquet_file_is_a_page(store):
    """A filter, an order and a keyset page — over a file."""
    page = store.search(
        Sales,
        Criteria(
            where=Condition(field="amount", operator=Operator.GTE, value=900),
            order=(Sort(field="at", descending=True), Sort(field="sale_id")),
            pagination=Pagination(limit=5),
        ),
    )

    assert len(page.items) == 5
    assert all(one.amount >= 900 for one in page)
    assert [one.at for one in page] == sorted((one.at for one in page), reverse=True)
    assert page.cursor is not None, "a file pages by cursor like anything else"


def test_the_walk_reaches_every_row_of_the_file(store):
    """Proof that the page is a page and not a coincidence: the cursor walks the whole year."""
    walked = [
        one.sale_id
        for page in store.stream(Sales, Criteria(pagination=Pagination(limit=90)))
        for one in page
    ]

    assert len(walked) == DAYS
    assert len(set(walked)) == DAYS


def test_a_time_series_by_month_is_answered_by_the_file(store):
    """The grain reaches Parquet through the translator registered for DuckDB — which takes
    `strftime(column, format)`, the other way round from SQLite. That is the one thing that
    had to be taught."""
    buckets = store.group_by_levels(
        Sales,
        Criteria(
            grouping=Grouping(
                group_by=(Level(field="at", grain="month"),),
                measures={
                    "total": Measure(function="sum", field="amount"),
                    "biggest": Measure(function="max", field="amount"),
                },
            )
        ),
    )

    assert [one.value for one in buckets] == [f"2026-{month:02d}" for month in range(1, 13)]
    assert sum(one.count for one in buckets) == DAYS
    january = buckets[0]
    assert january.count == 31
    assert january.measures["total"] == sum(100 + (day * 37) % 900 for day in range(31))


def test_the_measures_a_database_computes_reach_a_file_too(store):
    """`count_distinct` and `percentile` are the two that do not compile to `func.<name>`.
    DuckDB answers both, so the refusal SQLite gets is about SQLite and not about this layer.
    """
    buckets = store.group_by_levels(
        Sales,
        Criteria(
            grouping=Grouping(
                group_by=(Level(field="region"),),
                measures={
                    "reps": Measure(function="count_distinct", field="rep"),
                    "middle": Measure(function="percentile", field="amount", argument=0.5),
                },
            )
        ),
    )

    assert len(buckets) == len(REGIONS)
    for bucket in buckets:
        assert bucket.measures["reps"] == 4, "the four reps cycle through every region"
        assert 100 <= bucket.measures["middle"] <= 999


def test_a_bucket_of_a_file_opens_like_any_other(store):
    """The claim that makes this worth having: what a screen does with a bucket does not change
    because the rows live in a file."""
    reading = Criteria(
        where=Condition(field="region", operator=Operator.EQ, value="north"),
        grouping=Grouping(
            group_by=(Level(field="at", grain="month"),),
            measures={"total": Measure(function="sum", field="amount")},
        ),
    )

    bucket = store.group_by_levels(Sales, reading)[0]
    inside = store.search(Sales, bucket.criteria)

    assert inside.count is not None and inside.count.value == bucket.count
    assert all(one.region == "north" for one in inside)
    assert all(one.at.strftime("%Y-%m") == bucket.value for one in inside)


def test_the_two_engines_answer_the_same_reading_the_same_way(store, parquet):
    """A reading answered over the file and over a table built from the same rows: same
    counts, same totals. Which is what makes the adapter an adapter and not a second product.
    """
    reading = Criteria(
        where=Condition(field="amount", operator=Operator.GT, value=500),
        grouping=Grouping(
            group_by=(Level(field="region"),),
            measures={"total": Measure(function="sum", field="amount")},
            order=(Sort(field="total", descending=True),),
        ),
    )
    over_the_file = store.group_by_levels(Sales, reading)

    table = Database("duckdb:///:memory:")
    with table.engine.begin() as connection:
        from sqlalchemy import text

        connection.execute(text(f"CREATE TABLE sale AS SELECT * FROM '{parquet}'"))
    over_a_table = Repository(table).group_by_levels(Sales, reading)

    assert [(one.value, one.count, one.measures["total"]) for one in over_the_file] == [
        (one.value, one.count, one.measures["total"]) for one in over_a_table
    ]
