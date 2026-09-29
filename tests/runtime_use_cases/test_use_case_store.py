"""`UseCaseStore`: the one contract every store meets — in memory and in a table — and replicas
that share a table load what any of them saved."""

from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import MetaData, inspect

from sincpro_framework.orm import Database
from sincpro_framework.orm.runtime_use_cases import SqlUseCases, use_case_table
from sincpro_framework.runtime_use_cases import (
    BusRegistry,
    InMemoryUseCases,
    RuntimeUseCase,
    UseCaseStore,
)
from tests.runtime_use_cases.billing import CHECKOUT, QUOTE, billing

QUOTE_COMMAND = "sincpro_runtime.billing.quote.CommandQuote"


def _sql_store(tmp_path: Path) -> SqlUseCases:
    metadata = MetaData()
    table = use_case_table(metadata)
    database = Database(f"sqlite:///{tmp_path / 'use_cases.sqlite3'}")
    metadata.create_all(database.engine)
    return SqlUseCases(database, table)


STORES: dict[str, Callable[[Path], UseCaseStore]] = {
    "memory": lambda _: InMemoryUseCases(),
    "sql": _sql_store,
}


@pytest.fixture(params=list(STORES))
def store(request: pytest.FixtureRequest, tmp_path: Path) -> UseCaseStore:
    return STORES[request.param](tmp_path)


def test_a_saved_use_case_comes_back_as_it_was_saved(store: UseCaseStore):
    quote = RuntimeUseCase(
        name="quote", source=QUOTE, version=3, replaces="billing.features.Quote"
    )

    store.save(quote)

    assert store.active() == [quote]


def test_saving_again_is_the_new_version_in_the_place_of_the_first(store: UseCaseStore):
    store.save(RuntimeUseCase(name="quote", source=QUOTE))
    store.save(RuntimeUseCase(name="checkout", source=CHECKOUT))

    store.save(RuntimeUseCase(name="quote", source=QUOTE.replace("1.13", "1.16"), version=2))

    assert [(one.name, one.version) for one in store.active()] == [
        ("quote", 2),
        ("checkout", 1),
    ]


def test_an_inactive_use_case_is_kept_but_not_active(store: UseCaseStore):
    store.save(RuntimeUseCase(name="quote", source=QUOTE))

    store.save(RuntimeUseCase(name="quote", source=QUOTE, version=2, active=False))

    assert store.active() == []


def test_replicas_sharing_a_table_load_what_any_of_them_saved(tmp_path: Path):
    metadata = MetaData()
    table = use_case_table(metadata)
    database = Database(f"sqlite:///{tmp_path / 'shared.sqlite3'}")
    metadata.create_all(database.engine)
    one = BusRegistry(billing, SqlUseCases(database, table))
    other = BusRegistry(billing, SqlUseCases(database, table))

    one.store.save(RuntimeUseCase(name="quote", source=QUOTE))
    other.reload()

    assert other.execute(QUOTE_COMMAND, {"amount": 100}).total == Decimal("113.00")


def test_the_table_is_the_projects_to_name_and_to_migrate():
    metadata = MetaData()

    table = use_case_table(metadata, "billing_use_case")

    assert metadata.tables["billing_use_case"] is table
    assert {column.name for column in table.columns} == {
        "name",
        "position",
        "source",
        "version",
        "active",
        "replaces",
        "saved_at",
    }
    assert [column.name for column in inspect(table).primary_key] == ["name"]
