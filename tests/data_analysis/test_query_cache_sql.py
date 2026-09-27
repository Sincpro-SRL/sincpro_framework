"""On a real database: the statements that reach it are the pages not held — and a repository
narrowed to one tenant never answers from what another tenant's repository read."""

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Column, Integer, Text, event
from sqlalchemy.orm import registry

from sincpro_framework.data_analysis import QueryCache
from sincpro_framework.ddd import Criteria, Entity
from sincpro_framework.orm import Database, Repository, entity_table, map_aggregates


@dataclass
class Order(Entity):
    tenant: str = ""
    total: int = 0


MAPPING = registry()
TABLE = entity_table(
    "orders", MAPPING.metadata, Column("tenant", Text), Column("total", Integer)
)
map_aggregates(MAPPING, {Order: TABLE})


@pytest.fixture
def database(tmp_path: Path) -> Database:
    table = TABLE
    database = Database(f"sqlite:///{tmp_path / 'orders.sqlite3'}")
    MAPPING.metadata.create_all(database.engine)
    now = datetime(2026, 9, 27, tzinfo=UTC)
    with database.engine.begin() as connection:
        connection.execute(
            table.insert(),
            [
                {
                    "id": f"o{n:04d}",
                    "tenant": "acme" if n % 2 else "globex",
                    "total": n,
                    "created_at": now,
                    "updated_at": now,
                    "version": 1,
                }
                for n in range(400)
            ],
        )
    return database


def _selects(database: Database) -> list[str]:
    seen: list[str] = []
    event.listen(database.engine, "before_cursor_execute", lambda *call: seen.append(call[2]))
    return seen


PAGE = Criteria.model_validate({"order": [{"field": "id"}], "pagination": {"limit": 50}})


def test_only_the_pages_not_held_reach_the_database(database: Database):
    repository = Repository(database)
    cache = QueryCache()
    statements = _selects(database)

    cache.fetch(repository, Order, PAGE, pages=2)
    cache.fetch(repository, Order, PAGE, pages=2)
    cache.fetch(repository, Order, PAGE, pages=3)

    assert len(statements) == 3 and all(
        one.lstrip().upper().startswith("SELECT") for one in statements
    )


def test_a_repository_of_another_tenant_never_answers_from_what_was_held(database: Database):
    acme = Repository(database).narrowed(
        Criteria.model_validate(
            {"where": {"field": "tenant", "operator": "=", "value": "acme"}}
        )
    )
    globex = Repository(database).narrowed(
        Criteria.model_validate(
            {"where": {"field": "tenant", "operator": "=", "value": "globex"}}
        )
    )
    cache = QueryCache()

    ours = cache.fetch_all(acme, Order, PAGE)
    theirs = cache.fetch_all(globex, Order, PAGE)

    assert set(ours.column("tenant")) == {"acme"} and set(theirs.column("tenant")) == {
        "globex"
    }
    assert len(cache) == 2
