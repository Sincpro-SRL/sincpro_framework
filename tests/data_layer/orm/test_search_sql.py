"""A page over a statement the caller wrote: the join the framework does not build, paged,
filtered, ordered and counted by the criteria as if it were a table.

The statement joins `thing` to a second table of labels. Every value a criteria carries is bound,
so a hostile value finds nothing rather than changing the statement. Runs on each engine the suite
is given.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import Column, Integer, MetaData, Table, Text, column, text

from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.repository import Repository
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
from .models import (
    EPOCH,
    ROW_COUNT,
    Client,
    Thing,
    Things,
    client_table,
    mapper_registry,
    thing_table,
)

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
).columns(column("thing_id", Text), column("label", Text), column("size", Integer))


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


def test_a_field_the_statement_does_not_return_is_dropped_and_reported(joined):
    """As `search` answers a stale saved filter: the rest still runs, and the caller is told."""
    asked = Criteria(selection=Condition(field="colour", value="red"))

    page = joined.search_sql(Things, JOINED, asked)
    report = joined.query_page(REPORT, "thing_id", asked)

    assert len(page) == ROW_COUNT
    assert [one.reason for one in page.dropped] == ["unknown_field"]
    assert [one.reason for one in report.dropped] == ["unknown_field"]


def test_a_date_sent_as_text_is_read_as_the_date_it_names(joined):
    """A date travels in JSON as text; it is read as the field's type, not compared as text."""
    since = (EPOCH + timedelta(minutes=10)).isoformat()
    sent = Criteria.model_validate(
        {
            "selection": {"field": "made_at", "operator": ">=", "value": since},
            "pagination": {"limit": 50},
        }
    )

    assert len(joined.search_sql(Things, JOINED, sent)) == ROW_COUNT - 10


def test_a_dynamic_value_is_read_before_the_statement_runs(joined):
    sent = Criteria.model_validate(
        {
            "selection": {"field": "made_at", "operator": "<=", "value": {"$": "today"}},
            "pagination": {"limit": 50},
        }
    )

    assert len(joined.search_sql(Things, JOINED, sent)) == ROW_COUNT


def test_a_narrowed_repository_reads_a_statement_within_its_scope(joined):
    """A scope is what a tenant or a permission is: a statement does not read past it."""
    narrowed = joined.narrowed(Criteria(selection=Condition(field="owner", value="owner-1")))

    page = narrowed.search_sql(Things, JOINED, Criteria(pagination=Pagination(limit=50)))

    assert {one.owner for one in page} == {"owner-1"}
    assert len(page) == len(range(1, ROW_COUNT, 3))


def test_a_report_that_cannot_answer_the_scope_is_refused_rather_than_read_wide(joined):
    narrowed = joined.narrowed(Criteria(selection=Condition(field="owner", value="owner-1")))

    with pytest.raises(ProgrammingError, match="cannot answer the scope"):
        narrowed.query_page(REPORT, "thing_id")


def test_an_archived_record_is_left_out_of_a_statement_unless_asked(engine_url):
    repository = Repository(fresh(engine_url, mapper_registry.metadata))
    live, gone = Client(name="Live"), Client(name="Gone")
    repository.save([live, gone])
    repository.archive(gone)
    every_client = text("SELECT * FROM client").columns(*client_table.c)

    assert [one.name for one in repository.search_sql(Client, every_client)] == ["Live"]


def test_a_report_without_its_identity_is_refused(joined):
    with pytest.raises(ProgrammingError, match="does not return its identity 'id'"):
        joined.query_page(REPORT, "id")


def test_a_statement_missing_a_mapped_column_is_refused(joined):
    narrow = text("SELECT t.thing_id, t.name FROM thing t").columns(
        thing_table.c.thing_id, thing_table.c.name
    )

    with pytest.raises(ProgrammingError, match="does not return"):
        joined.search_sql(Things, narrow)


def test_a_report_pages_rows_with_no_entity_to_read_them_as(joined):
    page = joined.query_page(
        REPORT,
        "thing_id",
        Criteria(
            selection=impressions().selection,
            order=Sort.parse("-size"),
            pagination=Pagination(limit=4),
            count=CountMode.EXACT,
        ),
    )

    assert len(page.rows) == 4
    assert all(row["label"] == "Impresión" for row in page.rows)
    assert page.count.value == 9
    assert page.cursor is not None


def test_a_report_walk_yields_every_row_once(joined):
    criteria = Criteria(order=Sort.parse("-size"), pagination=Pagination(limit=4))
    seen: list[str] = []
    page = joined.query_page(REPORT, "thing_id", criteria)
    while True:
        seen.extend(row["thing_id"] for row in page.rows)
        if page.cursor is None:
            break
        page = joined.query_page(REPORT, "thing_id", criteria.resuming_from(page.cursor))

    assert len(seen) == ROW_COUNT
    assert len(set(seen)) == ROW_COUNT


def test_a_report_hostile_value_is_bound_not_written(joined):
    page = joined.query_page(
        REPORT,
        "thing_id",
        Criteria(
            selection=Condition(
                field="label", operator=Operator.EQ, value="'; DROP TABLE thing; --"
            ),
            count=CountMode.EXACT,
        ),
    )

    assert page.rows == ()
    assert page.count.value == 0
    untouched = joined.search_sql(Things, JOINED, Criteria(pagination=Pagination(limit=1)))
    assert untouched.count.value == ROW_COUNT  # the table was not dropped


def test_a_timestamp_is_the_datetime_the_model_holds(joined):
    page = joined.search_sql(Things, JOINED, Criteria(pagination=Pagination(limit=1)))

    assert isinstance(page.items[0].made_at, datetime)


@pytest.mark.parametrize(
    "written",
    [
        "UPDATE thing SET name = 'x'",
        "SELECT t.thing_id FROM thing t FOR UPDATE",
        "SELECT t.thing_id FROM thing t; DELETE FROM thing",
        "WITH x AS (SELECT 1) UPDATE thing SET name = 'x'",
    ],
)
def test_a_statement_that_writes_or_locks_is_refused(joined, written):
    statement = text(written).columns(column("thing_id", Text))

    with pytest.raises(ProgrammingError, match="must be one read"):
        joined.search_sql(Things, statement)
    with pytest.raises(ProgrammingError, match="must be one read"):
        joined.query_page(statement, "thing_id")
