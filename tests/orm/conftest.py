"""Fixtures for the persistence layer: a fresh store per test, and a way to count what SQL runs.

**One database per test, in memory.** A shared store lets one test's writes leak into the next
and turns an ordering accident into a failure nobody can reproduce; twenty-five rows cost
nothing to insert again.

**`queries_run` is the fixture the design depends on.** Every promise this layer makes about
the N+1 reduces to a number of statements, and a number is only a guarantee if something
asserts it. Without this, a relation that quietly starts loading per row still passes every
test about what comes back.
"""

from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, contextmanager

import pytest
from sqlalchemy import event

from sincpro_framework.orm.sqlalchemy.database import Database
from sincpro_framework.orm.sqlalchemy.repository import Repository

from .models import ROW_COUNT, Thing, a_thing, mapper_registry


@pytest.fixture
def database() -> Database:
    """An empty in-memory SQLite with the test tables created."""
    fresh = Database("sqlite://")
    mapper_registry.metadata.create_all(fresh.engine)
    return fresh


@pytest.fixture
def things() -> list[Thing]:
    """The twenty-five things the store is populated with, as the factory made them."""
    return [a_thing(number) for number in range(ROW_COUNT)]


@pytest.fixture
def store(database: Database, things: list[Thing]) -> Repository:
    """The repository over a populated database."""
    records = Repository(database)
    with records.context() as repository:
        for thing in things:
            repository.save(thing)
    return records


@pytest.fixture
def queries_run(store: Repository) -> Callable[[], AbstractContextManager[list[str]]]:
    """Counts the statements a block emits. The guard against the N+1 coming back."""

    @contextmanager
    def counting() -> Generator[list[str]]:
        statements: list[str] = []

        def record(connection, cursor, statement, parameters, context, many) -> None:
            statements.append(statement)

        event.listen(store.database.engine, "before_cursor_execute", record)
        try:
            yield statements
        finally:
            event.remove(store.database.engine, "before_cursor_execute", record)

    return counting
