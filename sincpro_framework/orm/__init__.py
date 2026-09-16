"""Persistence: running a `Criteria` against a database and keeping what a use case built.

    from sincpro_framework.orm import Database, Repository, entity_table, map_aggregates

An optional extra, like `[opentelemetry]` and `[rpc]`: the vocabulary in `sincpro_framework.ddd`
needs no database, and a service that only speaks it never installs one. Importing this package
without the extra says which one to install rather than failing on a missing `sqlalchemy`.

One backend today, SQLAlchemy, under `sincpro_framework.orm.sqlalchemy`. There is no
`Protocol` in front of it on purpose: an interface with one implementation is a promise nobody
tests, and the second backend is what would earn it. What a project still owns — which database
each bounded context talks to, and its migrations — is in `docs/design/persistence.md`.
"""

SQLALCHEMY_MISSING = (
    "SQLAlchemy is not installed. Install with: pip install sincpro-framework[sqlalchemy]"
)

try:
    import sqlalchemy as _sqlalchemy  # noqa: F401
except (
    ImportError
) as error:  # pragma: no cover - exercised by tests/orm/test_optional_extra.py
    raise ImportError(SQLALCHEMY_MISSING) from error

from sincpro_framework.orm.sqlalchemy import (
    Database,
    JsonText,
    Repository,
    TranslatedText,
    describe,
    entity_columns,
    entity_table,
    map_aggregates,
    register_grain_translator,
)

__all__ = [
    "SQLALCHEMY_MISSING",
    "Database",
    "JsonText",
    "Repository",
    "TranslatedText",
    "describe",
    "entity_columns",
    "entity_table",
    "map_aggregates",
    "register_grain_translator",
]
