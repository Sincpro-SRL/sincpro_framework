"""A page ordered by a nullable column walks every row once, in the order the database gives.

NULL has a place in an order: first ascending, last descending. A keyset that compared it as a
value would drop the rows holding NULL from a page — the failure the old refusal existed to
prevent. These walks run over both the SQL repository and the in-memory one, so the two are held
to the same placement and the same cursor.
"""

import pytest
from sqlalchemy.dialects import postgresql

from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.repository import Repository
from sincpro_framework.data_layer.repositories.memory_repository import MemoryRepository
from sincpro_framework.ddd.criteria import Criteria, Pagination, Sort

from .engines import ENGINES, fresh
from .models import ROW_COUNT, Thing, Things, mapper_registry


@pytest.fixture(params=["memory", *ENGINES])
def repository(request, things: list[Thing]):
    """The same twenty-five things, held by the in-memory repository and by each database."""
    if request.param == "memory":
        held = MemoryRepository()
    else:
        held = Repository(fresh(request.param, mapper_registry.metadata))
    with held.context() as repository:
        for thing in things:
            repository.save(thing)
    return held


def walk(repository, criteria: Criteria) -> list[str]:
    """Every id the pages yield, following the cursors to the end."""
    seen: list[str] = []
    page = repository.search(Things, criteria)
    while True:
        seen.extend(page.ids)
        if page.cursor is None:
            return seen
        page = repository.search(Things, criteria.resuming_from(page.cursor))


def everything_in_one_page(repository, order: str) -> list[str]:
    return repository.search(
        Things, Criteria(order=Sort.parse(order), pagination=Pagination(limit=ROW_COUNT))
    ).ids


@pytest.mark.parametrize("order", ["owner", "-owner", "owner,-size", "-owner,size"])
def test_walking_a_nullable_ordering_yields_each_row_once(repository, order):
    seen = walk(repository, Criteria(order=Sort.parse(order), pagination=Pagination(limit=4)))

    assert len(seen) == ROW_COUNT
    assert len(set(seen)) == ROW_COUNT


@pytest.mark.parametrize("order", ["owner", "-owner", "owner,-size", "-owner,size"])
def test_the_pages_join_up_to_the_same_order_as_one_page(repository, order):
    """The keyset is not a second ordering: pages laid end to end are the whole ordering."""
    walked = walk(
        repository, Criteria(order=Sort.parse(order), pagination=Pagination(limit=4))
    )

    assert walked == everything_in_one_page(repository, order)


def test_null_comes_first_ascending_and_last_descending(repository):
    ascending = everything_in_one_page(repository, "owner")
    descending = everything_in_one_page(repository, "-owner")

    nulls = [f"th_{number:04d}" for number in range(0, ROW_COUNT, 3)]
    assert ascending[: len(nulls)] == nulls
    assert descending[-len(nulls) :] == list(reversed(nulls))  # the tiebreak runs down too


def test_a_walk_descending_over_a_nullable_column_reaches_the_null_rows(repository):
    """The rows holding NULL are the last ones of a descending walk; a comparison that
    treated NULL as a value would stop short of them."""
    seen = walk(
        repository, Criteria(order=Sort.parse("-owner"), pagination=Pagination(limit=5))
    )

    assert {f"th_{number:04d}" for number in range(0, ROW_COUNT, 3)} <= set(seen)


@pytest.fixture
def sqlite_store(things: list[Thing]) -> Repository:
    store = Repository(fresh("sqlite://", mapper_registry.metadata))
    with store.context() as repository:
        for thing in things:
            repository.save(thing)
    return store


def written_for_postgres(store: Repository, criteria: Criteria) -> str:
    """The statement a page runs, as PostgreSQL would be sent it."""
    return str(store.statement(Things, criteria).compile(dialect=postgresql.dialect()))


def test_a_field_that_is_never_null_is_ordered_without_placing_null(sqlite_store):
    """PostgreSQL reads `thing_id DESC NULLS LAST` as an order no ordinary index gives, and
    sorts the whole table for every page instead of walking the primary key."""
    written = written_for_postgres(sqlite_store, Criteria(pagination=Pagination(limit=4)))

    assert "ORDER BY thing.thing_id DESC" in written
    assert "NULLS" not in written


def test_only_the_nullable_field_places_null(sqlite_store):
    written = written_for_postgres(
        sqlite_store, Criteria(order=Sort.parse("owner"), pagination=Pagination(limit=4))
    )

    assert "ORDER BY thing.owner ASC NULLS FIRST, thing.thing_id" in written
    assert written.count("NULLS") == 1


@pytest.mark.parametrize(
    ("order", "comparison"),
    [
        ("made_at", "(thing.made_at, thing.thing_id) >"),
        ("-made_at", "(thing.made_at, thing.thing_id) <"),
    ],
)
def test_the_next_page_of_fields_never_null_is_one_row_value_comparison(
    sqlite_store, order, comparison
):
    """One direction and no NULL: the comparison a planner reads as a range over the index."""
    criteria = Criteria(order=Sort.parse(order), pagination=Pagination(limit=4))
    cursor = sqlite_store.search(Things, criteria).cursor

    written = written_for_postgres(sqlite_store, criteria.resuming_from(cursor))

    assert comparison in written
    assert " OR " not in written
