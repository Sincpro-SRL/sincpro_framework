"""An extension that adds columns to a table that is not its own: each chain keeps what it declares.

The core migrates `move(number, total)`. A module migrates `move_delivery` and the column
`move.zone`. Without a rule, the module's autogenerate would drop the core's columns (it declares
only its own) and the core's next step would drop `zone`. `docs/spec/extensibility.md` §8.
"""

from pathlib import Path

import pytest
from sqlalchemy import Column, ForeignKey, MetaData, Table, Text, inspect

from sincpro_framework.data_layer.migrations import ContextMigrations, Migrations
from sincpro_framework.data_layer.orm import AlembicEngine, Database

from .engines import fresh


def core_tables(*extra: str) -> MetaData:
    metadata = MetaData()
    Table(
        "move",
        metadata,
        Column("id", Text, primary_key=True),
        Column("number", Text),
        *[Column(name, Text) for name in extra],
    )
    return metadata


def module_tables() -> MetaData:
    metadata = MetaData()
    Table("move", metadata, Column("id", Text, primary_key=True), Column("zone", Text))
    Table(
        "move_delivery",
        metadata,
        Column("id", Text, ForeignKey("move.id"), primary_key=True),
        Column("route", Text),
    )
    return metadata


@pytest.fixture
def database(tmp_path: Path, engine_url: str) -> Database:
    """SQLite as a file (a migration needs the same database across connections), or the server
    with an emptied `public` schema."""
    if engine_url == "sqlite://":
        return Database(f"sqlite:///{tmp_path / 'erp.sqlite3'}")
    return fresh(engine_url, MetaData())


def project(tmp_path: Path, database: Database, core: MetaData):
    sales = ContextMigrations("sales", tmp_path / "sales")
    sales.store("main", AlembicEngine(core, database))
    delivery = ContextMigrations("sales_delivery", tmp_path / "delivery")
    delivery.store("main", AlembicEngine(module_tables(), database))
    return Migrations([sales, delivery])


def body(migrations: Migrations, context: str, step) -> str:
    """What the step does going forward: `upgrade` only, since `downgrade` undoes it."""
    written = (migrations.chain(context, "main").folder / step.file).read_text()
    return written.split("def downgrade")[0]


def columns(database: Database, table: str) -> set[str]:
    return {one["name"] for one in inspect(database.engine).get_columns(table)}


def test_the_modules_step_adds_its_column_and_drops_nothing_of_the_core(tmp_path, database):
    migrations = project(tmp_path, database, core_tables())
    created = migrations.revision("sales", "main", "create move")
    migrations.upgrade()

    step = migrations.revision("sales_delivery", "main", "delivery", requires=[created.key])
    migrations.upgrade()

    written = body(migrations, "sales_delivery", step)
    assert "Column('zone'" in written and "create_table('move_delivery'" in written
    assert "drop_column" not in written
    assert columns(database, "move") == {"id", "number", "zone"}


def test_the_cores_next_step_does_not_drop_a_column_a_module_declares(tmp_path, database):
    core = core_tables()
    migrations = project(tmp_path, database, core)
    created = migrations.revision("sales", "main", "create move")
    migrations.upgrade()
    migrations.revision("sales_delivery", "main", "delivery", requires=[created.key])
    migrations.upgrade()

    core.tables["move"].append_column(Column("due", Text))  # the core ships a minor
    step = migrations.revision("sales", "main", "add due")
    migrations.upgrade()

    written = body(migrations, "sales", step)
    assert "Column('due'" in written and "drop_column" not in written
    assert columns(database, "move") == {"id", "number", "zone", "due"}


def test_a_column_nobody_else_declares_is_still_dropped_by_the_chain_that_owns_it(
    tmp_path, database
):
    core = core_tables("legacy")
    migrations = project(tmp_path, database, core)
    created = migrations.revision("sales", "main", "create move")
    migrations.upgrade()
    migrations.revision("sales_delivery", "main", "delivery", requires=[created.key])
    migrations.upgrade()

    core.tables["move"]._columns.remove(core.tables["move"].c.legacy)  # the core drops it
    step = migrations.revision("sales", "main", "drop legacy")
    migrations.upgrade()

    written = body(migrations, "sales", step)
    assert "drop_column" in written and "'legacy'" in written and "'zone'" not in written
    assert columns(database, "move") == {"id", "number", "zone"}


def test_undoing_the_module_removes_its_column_and_its_table_and_leaves_the_core(
    tmp_path, database
):
    migrations = project(tmp_path, database, core_tables())
    created = migrations.revision("sales", "main", "create move")
    migrations.upgrade()
    step = migrations.revision("sales_delivery", "main", "delivery", requires=[created.key])
    migrations.upgrade()

    migrations.downgrade(to=created.id)

    assert columns(database, "move") == {"id", "number"}
    assert "move_delivery" not in inspect(database.engine).get_table_names()


def test_neither_chain_reports_drift_for_what_the_other_declares(tmp_path, database):
    migrations = project(tmp_path, database, core_tables())
    created = migrations.revision("sales", "main", "create move")
    migrations.upgrade()
    migrations.revision("sales_delivery", "main", "delivery", requires=[created.key])
    migrations.upgrade()

    assert migrations.check() == []
