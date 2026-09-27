"""`AlembicEngine`: the engine the framework ships for SQL stores.

Two contexts on one SQLite database: `common` owns `partner`, `billing` owns `invoice` with a
foreign key to it. Each chain keeps its own version table, autogenerate looks only at the
context's own tables, and the orchestrator runs them in one order.

A foreign key into another context names that context's column — `ForeignKey(partner.c.id)` —
not the string `"partner.id"`, which SQLAlchemy resolves only inside one `MetaData`.
"""

from pathlib import Path

import pytest
from sqlalchemy import Column, ForeignKey, Integer, MetaData, String, Table, inspect

from sincpro_framework.migrations import (
    Chain,
    ChainState,
    ContextMigrations,
    MigrationFailed,
    MigrationRefused,
    Migrations,
)
from sincpro_framework.orm import Database
from sincpro_framework.orm.migrations import AlembicEngine, version_table


def _common_tables() -> MetaData:
    metadata = MetaData()
    Table(
        "partner",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("name", String(80)),
    )
    return metadata


def _billing_tables(common_tables: MetaData) -> MetaData:
    partner = common_tables.tables["partner"]
    metadata = MetaData()
    Table(
        "invoice",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("partner_id", Integer, ForeignKey(partner.c.id)),
    )
    return metadata


@pytest.fixture
def database(tmp_path: Path) -> Database:
    return Database(f"sqlite:///{tmp_path / 'erp.sqlite3'}")


def _migrations(tmp_path: Path, database: Database, common_tables: MetaData) -> Migrations:
    common = ContextMigrations("common", tmp_path / "common")
    common.store("main", AlembicEngine(common_tables, database))
    billing = ContextMigrations("billing", tmp_path / "billing")
    billing.store("main", AlembicEngine(_billing_tables(common_tables), database))
    return Migrations([common, billing])


def _tables(database: Database) -> set[str]:
    return set(inspect(database.engine).get_table_names())


def test_each_context_migrates_its_own_tables_with_its_own_version_table(tmp_path, database):
    migrations = _migrations(tmp_path, database, _common_tables())
    partner = migrations.revision("common", "main", "create partner")
    migrations.upgrade()
    invoice = migrations.revision("billing", "main", "create invoice", requires=[partner.key])

    body = migrations.chain("billing", "main").folder / invoice.file
    assert "create_table('partner'" not in body.read_text()

    migrations.upgrade()

    assert {"partner", "invoice"} <= _tables(database)
    assert {"alembic_version__common__main", "alembic_version__billing__main"} <= _tables(
        database
    )
    assert {one.state for one in migrations.status().chains.values()} == {
        ChainState.UP_TO_DATE
    }


def test_a_step_body_is_named_by_its_id(tmp_path, database):
    migrations = _migrations(tmp_path, database, _common_tables())

    step = migrations.revision("common", "main", "create partner")

    assert Path(step.file).name.startswith(step.id)
    body = migrations.chain("common", "main").folder / step.file
    assert f'revision = "{step.id}"' in body.read_text()


def test_downgrade_to_base_drops_what_every_context_created(tmp_path, database):
    migrations = _migrations(tmp_path, database, _common_tables())
    migrations.revision("common", "main", "create partner")
    migrations.upgrade()
    migrations.revision("billing", "main", "create invoice")
    migrations.upgrade()

    reverted = migrations.downgrade(to="base")

    assert [step.message for step in reverted] == ["create invoice", "create partner"]
    assert "partner" not in _tables(database) and "invoice" not in _tables(database)


def test_a_column_declared_without_a_revision_is_reported_as_drift(tmp_path, database):
    common_tables = _common_tables()
    migrations = _migrations(tmp_path, database, common_tables)
    migrations.revision("common", "main", "create partner")
    migrations.upgrade()
    migrations.revision("billing", "main", "create invoice")
    migrations.upgrade()
    assert migrations.check() == []

    common_tables.tables["partner"].append_column(Column("tax_id", String(20)))

    problems = migrations.check()
    assert "common/main: column partner.tax_id is declared but no step adds it" in problems


def test_a_store_behind_its_steps_is_not_checked_for_drift(tmp_path, database):
    migrations = _migrations(tmp_path, database, _common_tables())
    migrations.revision("common", "main", "create partner")
    migrations.upgrade()
    migrations.revision("billing", "main", "create invoice")

    assert migrations.check() == []  # a table no step created yet is not drift


def test_resolve_records_where_the_store_really_stands(tmp_path, database):
    migrations = _migrations(tmp_path, database, _common_tables())
    first = migrations.revision("common", "main", "create partner")
    migrations.upgrade()

    migrations.resolve("common", "main", at=None)
    assert migrations.status().chains["common/main"].state == ChainState.BEHIND

    migrations.resolve("common", "main", at=first.id)
    assert migrations.status().chains["common/main"].state == ChainState.UP_TO_DATE


def test_a_step_that_fails_part_way_on_sqlite_leaves_the_store_dirty(tmp_path, database):
    migrations = _migrations(tmp_path, database, _common_tables())
    first = migrations.revision("common", "main", "create partner")
    migrations.upgrade()
    broken = migrations.revision("common", "main", "half done")
    body = migrations.chain("common", "main").folder / broken.file
    body.write_text(f'''"""half done"""

import sqlalchemy as sa
from alembic import op

revision = "{broken.id}"
down_revision = "{first.id}"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("t1", sa.Column("id", sa.Integer))
    raise RuntimeError("boom")


def downgrade() -> None:
    op.drop_table("t1")
''')
    migrations.hash()

    with pytest.raises(MigrationFailed, match="half done"):
        migrations.upgrade()

    assert "t1" in _tables(database)  # SQLite does not take DDL back
    status = migrations.status().chains["common/main"]
    assert status.state == ChainState.DIRTY and status.position.head == first.id


def test_version_tables_of_different_chains_never_share_a_name():
    folder = Path(".")

    assert version_table(Chain("a_b", "c", folder)) != version_table(
        Chain("a", "b_c", folder)
    )
    with pytest.raises(ValueError, match="63"):
        version_table(Chain("a" * 40, "b" * 30, folder))


def test_a_store_with_several_heads_is_refused(tmp_path, database):
    migrations = _migrations(tmp_path, database, _common_tables())
    migrations.revision("common", "main", "create partner")
    migrations.upgrade()
    table = version_table(migrations.chain("common", "main"))
    with database.engine.begin() as connection:
        connection.exec_driver_sql(f"INSERT INTO {table} (version_num) VALUES ('other')")

    with pytest.raises(MigrationRefused, match="common/main.*several heads"):
        migrations.status()


def test_a_revision_on_a_store_that_is_behind_is_refused_and_records_nothing(
    tmp_path, database
):
    migrations = _migrations(tmp_path, database, _common_tables())
    migrations.revision("common", "main", "create partner")

    with pytest.raises(MigrationRefused, match="common/main.*upgrade"):
        migrations.revision("common", "main", "second")

    assert [step.message for step, _ in migrations.status().timeline] == ["create partner"]
