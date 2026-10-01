"""The engines a suite runs on: SQLite always, and a Postgres when one is given.

    SINCPRO_POSTGRES_URL=postgresql+psycopg://postgres:test@localhost:55432/fw make test

The suites that depend on what the engine does — isolation, locks, its error codes, a foreign
key it enforces — take the `engine_url` fixture and run once per engine.
"""

import os

from sqlalchemy import MetaData, text

from sincpro_framework.orm.sqlalchemy.infrastructure.database import Database

POSTGRES_URL = os.environ.get("SINCPRO_POSTGRES_URL")

ENGINES = ["sqlite://", *([POSTGRES_URL] if POSTGRES_URL else [])]


def fresh(url: str, metadata: MetaData, **options) -> Database:
    """A database with exactly these tables, empty: created in memory on SQLite; on a server,
    in a `public` schema emptied first, so another suite's tables never stand in the way."""
    database = Database(url, **options)
    if database.engine.dialect.name == "postgresql":
        with database.engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
    metadata.create_all(database.engine)
    return database
