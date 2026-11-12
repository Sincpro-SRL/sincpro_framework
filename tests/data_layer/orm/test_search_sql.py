"""A page over a statement the caller wrote: the join the framework does not build, paged,
filtered, ordered and counted by the criteria as if it were a table.

The statement joins `thing` to a second table of labels. Every value a criteria carries is bound,
so a hostile value finds nothing rather than changing the statement. Runs on each engine the suite
is given.
"""

from datetime import datetime

import pytest
from sqlalchemy import Column, MetaData, Table, Text, column, text

from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.repository import Repository
from sincpro_framework.ddd import exceptions
from sincpro_framework.ddd.criteria import (
    Condition,
    CountMode,
    Criteria,
    Offset,
    Operator,
    Pagination,
    Sort,
)
from sincpro_framework.exceptions import ProgrammingError

from .engines import fresh
from .models import ROW_COUNT, Thing, Things, mapper_registry, thing_table

LABELS = ["Impresión", "Factura", "Recibo"]

label_metadata = MetaData()
label_table = Table(
    "thing_label",
    label_metadata,
    Column("thing_id", Text, primary_key=True),
    Column("label", Text, nullable=False),
)

JOINED = text("""
    SELECT t.thing_id, t.name, t.size, t.tags, t.made_at, t.owner, l.label
    FROM thing t JOIN thing_label l ON l.thing_id = t.thing_id
    """).columns(*thing_table.c, column("label", Text))

REPORT = text(
    "SELECT t.thing_id, l.label, t.size FROM thing t JOIN thing_label l ON l.thing_id = t.thing_id"
).columns(column("thing_id", Text), column("label", Text), column("size"))


@pytest.fixture
def joined(engine_url, things):
    """The things, each labelled — the label of thing n is LABELS[n % 3] — behind a join."""
    database = fresh(engine_url, mapper_registry.metadata)
    label_table.create(database.engine)
    repository = Repository(database)
    with repository.context() as writes:
        for thing in things:
            writes.save(thing)
    with database.engine.begin() as connection:
        for number in range(ROW_COUNT):
            connection.execute(
                label_table.insert().values(
                    thing_id=f"th_{number:04d}", label=LABELS[number % len(LABELS)]
                )
            )
    return repository


def impressions() -> Criteria:
    """Those labelled `impresion`: every third thing, so nine of the twenty-five, by accent-free
    text."""
    return Criteria(
        selection=Condition(field="label", operator=Operator.LIKE, value="impresion")
    )


def walk(repository, criteria: Criteria, statement) -> list[str]:
    seen: list[str] = []
    page = repository.search_sql(Things, statement, criteria)
    while True:
        seen.extend(one.thing_id for one in page)
        if page.cursor is None:
            return seen
        page = repository.search_sql(Things, statement, criteria.resuming_from(page.cursor))


def test_the_criteria_filters_the_joined_rows_and_hydrates_the_model(joined):
    page = joined.search_sql(
        Things,
        JOINED,
        Criteria(selection=impressions().selection, pagination=Pagination(limit=50)),
    )

    assert len(page) == 9
    assert all(isinstance(one, Thing) for one in page)
    assert {one.thing_id for one in page} == {f"th_{n:04d}" for n in range(0, ROW_COUNT, 3)}


def test_walking_the_pages_yields_every_joined_row_once(joined):
    criteria = Criteria(pagination=Pagination(limit=4), order=Sort.parse("-size,thing_id"))

    seen = walk(joined, criteria, JOINED)

    assert len(seen) == ROW_COUNT
    assert len(set(seen)) == ROW_COUNT


def test_the_pages_join_up_to_the_one_page_of_the_same_order(joined):
    order = Sort.parse("size,-thing_id")
    walked = walk(joined, Criteria(pagination=Pagination(limit=4), order=order), JOINED)
    everything = joined.search_sql(
        Things, JOINED, Criteria(pagination=Pagination(limit=ROW_COUNT), order=order)
    )

    assert walked == [one.thing_id for one in everything]


def test_an_offset_skips_rows_of_the_statement(joined):
    page = joined.search_sql(
        Things,
        JOINED,
        Criteria(
            order=Sort.parse("thing_id"),
            pagination=Pagination(limit=5, strategy=Offset(rows=5)),
        ),
    )

    assert [one.thing_id for one in page] == [f"th_{n:04d}" for n in range(5, 10)]


def test_the_count_is_read_over_the_joined_rows(joined):
    page = joined.search_sql(
        Things,
        JOINED,
        Criteria(
            selection=impressions().selection,
            pagination=Pagination(limit=4),
            count=CountMode.EXACT,
        ),
    )

    assert page.count.value == 9
    assert page.count.exact


def test_a_hostile_value_is_a_parameter_and_finds_nothing(joined):
    page = joined.search_sql(
        Things,
        JOINED,
        Criteria(
            selection=Condition(field="label", operator=Operator.EQ, value="x' OR '1'='1"),
            count=CountMode.EXACT,
        ),
    )

    assert len(page) == 0
    assert page.count.value == 0


def test_a_criteria_naming_a_column_the_statement_lacks_is_refused(joined):
    with pytest.raises(exceptions.InvalidCriteria, match="no column 'colour'"):
        joined.search_sql(
            Things, JOINED, Criteria(selection=Condition(field="colour", value="red"))
        )


def test_a_statement_missing_a_mapped_column_is_refused(joined):
    narrow = text("SELECT t.thing_id, t.name FROM thing t").columns(
        thing_table.c.thing_id, thing_table.c.name
    )

    with pytest.raises(ProgrammingError, match="does not return"):
        joined.search_sql(Things, narrow)


def test_a_report_pages_rows_with_no_entity_to_read_them_as(joined):
    page = joined.query_page(
        REPORT,
        Criteria(
            selection=impressions().selection,
            order=Sort.parse("-size"),
            pagination=Pagination(limit=4),
            count=CountMode.EXACT,
        ),
        identity="thing_id",
    )

    assert len(page.rows) == 4
    assert all(row["label"] == "Impresión" for row in page.rows)
    assert page.count.value == 9
    assert page.cursor is not None


def test_a_report_walk_yields_every_row_once(joined):
    criteria = Criteria(order=Sort.parse("-size"), pagination=Pagination(limit=4))
    seen: list[str] = []
    page = joined.query_page(REPORT, criteria, identity="thing_id")
    while True:
        seen.extend(row["thing_id"] for row in page.rows)
        if page.cursor is None:
            break
        page = joined.query_page(
            REPORT, criteria.resuming_from(page.cursor), identity="thing_id"
        )

    assert len(seen) == ROW_COUNT
    assert len(set(seen)) == ROW_COUNT


def test_a_report_hostile_value_is_bound_not_written(joined):
    page = joined.query_page(
        REPORT,
        Criteria(
            selection=Condition(
                field="label", operator=Operator.EQ, value="'; DROP TABLE thing; --"
            ),
            count=CountMode.EXACT,
        ),
        identity="thing_id",
    )

    assert page.rows == ()
    assert page.count.value == 0
    untouched = joined.search_sql(Things, JOINED, Criteria(pagination=Pagination(limit=1)))
    assert untouched.count.value == ROW_COUNT  # the table was not dropped


def test_a_timestamp_is_the_datetime_the_model_holds(joined):
    page = joined.search_sql(Things, JOINED, Criteria(pagination=Pagination(limit=1)))

    assert isinstance(page.items[0].made_at, datetime)
