"""How the suite opens a database, in the test process and in a worker alike.

The tuning is what a provider would do for several writers on SQLite: WAL so a reader works
while a writer holds the file, a patient busy timeout so two writers queue instead of failing,
and connections shared across threads because the async bus runs a Feature on a worker thread.
Nothing here touches Postgres: a URL that is not SQLite is opened as it comes.
"""

from sqlalchemy import event

from sincpro_framework.orm.sqlalchemy.database import Database
from sincpro_framework.orm.sqlalchemy.repository import Repository


def tune(database: Database) -> Database:
    if database.engine.dialect.name != "sqlite":
        return database

    @event.listens_for(database.engine, "connect")
    def _pragmas(connection, _record) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    return database


def open_database(url: str) -> Database:
    if url.startswith("sqlite"):
        return tune(Database(url, connect_args={"check_same_thread": False, "timeout": 30}))
    return Database(url)


def open_ledger(url: str) -> Repository:
    return Repository(open_database(url))
