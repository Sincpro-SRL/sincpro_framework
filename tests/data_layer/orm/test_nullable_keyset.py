"""A page ordered by a nullable column walks every row once, in the order the database gives.

NULL has a place in an order: first ascending, last descending. A keyset that compared it as a
value would drop the rows holding NULL from a page — the failure the old refusal existed to
prevent. These walks run over both the SQL repository and the in-memory one, so the two are held
to the same placement and the same cursor.
"""

import pytest

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
