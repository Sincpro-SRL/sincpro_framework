"""SQLite with the defaults the other databases already have: a foreign key is enforced, so a
test on SQLite refuses the orphan Postgres would refuse in production — as Django and Rails do.
"""

from pathlib import Path

import pytest
from sqlalchemy import Column, ForeignKey, Integer, MetaData, Table, insert
from sqlalchemy.exc import IntegrityError

from sincpro_framework.orm import Database

METADATA = MetaData()
PARENT = Table("fk_parent", METADATA, Column("id", Integer, primary_key=True))
CHILD = Table(
    "fk_child",
    METADATA,
    Column("id", Integer, primary_key=True),
    Column("parent_id", Integer, ForeignKey(PARENT.c.id), nullable=False),
)


def test_sqlite_asked_to_enforce_foreign_keys_refuses_an_orphan(tmp_path: Path):
    database = Database(f"sqlite:///{tmp_path / 'fk.sqlite3'}", enforce_foreign_keys=True)
    METADATA.create_all(database.engine)

    with pytest.raises(IntegrityError), database.engine.begin() as connection:
        connection.execute(insert(CHILD).values(id=1, parent_id=404))


def test_a_child_of_a_real_parent_is_written(tmp_path: Path):
    database = Database(f"sqlite:///{tmp_path / 'fk.sqlite3'}", enforce_foreign_keys=True)
    METADATA.create_all(database.engine)

    with database.engine.begin() as connection:
        connection.execute(insert(PARENT).values(id=1))
        connection.execute(insert(CHILD).values(id=1, parent_id=1))


def test_by_default_sqlite_keeps_its_own_default(tmp_path: Path):
    database = Database(f"sqlite:///{tmp_path / 'fk.sqlite3'}")

    with database.engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 0
