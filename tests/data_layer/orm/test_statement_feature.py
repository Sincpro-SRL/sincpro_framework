"""A Query whose Feature answers from a statement the server wrote.

The statement lives on the Feature, never on the Query: a client sends a criteria over the columns
the statement returns and can send no SQL. The Feature pages it with `search_sql` and the cursor
goes back and forth exactly as for any paginated Query. Its permission is declared beside it, so
the bus refuses a caller who lacks it.
"""

import pytest
from sqlalchemy import Column, MetaData, Table, Text, column, text

from sincpro_framework import Feature, UseFramework
from sincpro_framework.auth import AccessControl, Identity, Permission, security
from sincpro_framework.auth.domain import exceptions as auth_exceptions
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.repository import Repository
from sincpro_framework.ddd import Criteria, Query, ResponsePaginatedQuery
from sincpro_framework.ddd.criteria import Condition, Operator, Pagination

from .engines import fresh
from .models import ROW_COUNT, Things, mapper_registry, thing_table

label_metadata = MetaData()
label_table = Table(
    "thing_label",
    label_metadata,
    Column("thing_id", Text, primary_key=True),
    Column("label", Text, nullable=False),
)
LABELS = ["Impresión", "Factura", "Recibo"]


class LabelPermission(Permission):
    READ = "labels.read"


class QueryLabelledThings(Query):
    """What a UI asks for: a criteria over the statement's columns. Nothing else."""


class ResponseLabelledThings(ResponsePaginatedQuery):
    things: list[dict]


def _bus(engine_url, things, guard: AccessControl | None = None) -> UseFramework:
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

    bus = UseFramework("labelled-things", log_after_execution=False)
    bus.add_dependency("repository", repository)
    declared = guard.requires(LabelPermission.READ) if guard is not None else _nothing

    @bus.feature(QueryLabelledThings)
    @declared
    class ListLabelledThings(Feature):
        """The server's statement: the join. The criteria filters, orders and pages it."""

        repository: Repository

        STATEMENT = text(
            "SELECT t.thing_id, t.name, t.size, t.tags, t.made_at, t.owner, l.label "
            "FROM thing t JOIN thing_label l ON l.thing_id = t.thing_id"
        ).columns(*thing_table.c, column("label", Text))

        def execute(self, dto: QueryLabelledThings) -> ResponseLabelledThings:
            page = self.repository.search_sql(Things, self.STATEMENT, dto.criteria)
            return ResponseLabelledThings(
                things=[{"id": one.thing_id, "name": one.name} for one in page],
                cursor=page.cursor,
            )

    if guard is not None:
        guard.on(bus)
    return bus


def _nothing(cls):
    return cls


@pytest.fixture
def billing(engine_url, things):
    return _bus(engine_url, things)


def _impressions() -> Criteria:
    return Criteria(
        selection=Condition(field="label", operator=Operator.LIKE, value="impresion"),
        pagination=Pagination(limit=50),
    )


def test_the_client_can_send_no_statement_only_a_criteria():
    assert set(QueryLabelledThings.model_fields) == {"criteria", "cursor"}


def test_a_criteria_filters_the_statement_the_server_wrote(billing):
    answer = billing(QueryLabelledThings(criteria=_impressions()), ResponseLabelledThings)

    # Every third thing carries the label `Impresión`. The filter read that column, though the
    # answer carries only the model's own fields.
    assert {row["id"] for row in answer.things} == {
        f"th_{number:04d}" for number in range(0, ROW_COUNT, 3)
    }


def test_pages_walk_the_statement_through_the_query(billing):
    query = QueryLabelledThings(criteria=Criteria(pagination=Pagination(limit=4)))

    pages = list(query.pages(billing, ResponseLabelledThings))
    seen = [row["id"] for page in pages for row in page.things]

    assert len(pages) == 7
    assert len(seen) == ROW_COUNT
    assert len(set(seen)) == ROW_COUNT


def test_a_caller_without_the_declared_permission_is_refused(engine_url, things):
    guard = AccessControl[LabelPermission]()
    bus = _bus(engine_url, things, guard)
    query = QueryLabelledThings(criteria=_impressions())

    with security.as_identity(Identity.user("user:9")), pytest.raises(
        auth_exceptions.PermissionDenied
    ) as refused:
        bus(query, ResponseLabelledThings)
    assert refused.value.requirement == LabelPermission.READ


def test_a_caller_holding_the_permission_gets_the_page(engine_url, things):
    guard = AccessControl[LabelPermission]()
    bus = _bus(engine_url, things, guard)
    reader = Identity.user("user:8", permissions={LabelPermission.READ})

    with security.as_identity(reader):
        answer = bus(QueryLabelledThings(criteria=_impressions()), ResponseLabelledThings)

    assert len(answer.things) == 9
