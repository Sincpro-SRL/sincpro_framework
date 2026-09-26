"""Persistence: running a `Criteria` against a database and keeping what a use case built.

    from sincpro_framework.orm import Database, Repository, entity_table, map_aggregates

An optional extra, like `[opentelemetry]` and `[rpc]`: the vocabulary in `sincpro_framework.ddd`
needs no database, and a service that only speaks it never installs one. Importing this package
without the extra says which one to install rather than failing on a missing `sqlalchemy`.

One backend today, SQLAlchemy, under `sincpro_framework.orm.sqlalchemy`. What it implements is
`ddd.repositories.Repository`, an abstract class rather than a `Protocol`: structural typing
checks names and not signatures, so an implementation with the wrong arguments passed
`isinstance` and failed where it was called. What a project still owns — which database each
bounded context talks to, and its migrations — is in `docs/persistence/reference.md`.
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
    Explained,
    JsonText,
    Relation,
    Repository,
    TranslatedText,
    archive_columns,
    audit_columns,
    delivery_columns,
    describe,
    entity_columns,
    entity_table,
    event_columns,
    map_aggregates,
    register_grain_translator,
)

__all__ = [
    "SQLALCHEMY_MISSING",
    "Database",
    "Explained",
    "JsonText",
    "Relation",
    "Repository",
    "TranslatedText",
    "archive_columns",
    "audit_columns",
    "delivery_columns",
    "describe",
    "entity_columns",
    "event_columns",
    "entity_table",
    "map_aggregates",
    "register_grain_translator",
]
