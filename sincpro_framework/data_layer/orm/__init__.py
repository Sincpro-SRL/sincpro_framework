"""Persistence: running a `Criteria` against a database and keeping what a use case built.

    from sincpro_framework.data_layer.orm import Database, Repository, map_aggregates, template_table

An optional extra, like `[opentelemetry]` and `[rpc]`: the vocabulary in `sincpro_framework.ddd`
needs no database, and a service that only speaks it never installs one. Importing this package
without the extra says which one to install rather than failing on a missing `sqlalchemy`.

One backend today, SQLAlchemy, under `sincpro_framework.data_layer.orm.sqlalchemy`. What it implements is
`ddd.repositories.Repository`, an abstract class rather than a `Protocol`: structural typing
checks names and not signatures, so an implementation with the wrong arguments passed
`isinstance` and failed where it was called. What a project still owns — which database each
bounded context talks to, and its migrations — is in `docs/persistence/reference.md`.
"""

from typing import TYPE_CHECKING, Any

SQLALCHEMY_MISSING = (
    "SQLAlchemy is not installed. Install with: pip install sincpro-framework[sqlalchemy]"
)

try:
    import sqlalchemy  # noqa: F401
except (
    ImportError
) as error:  # pragma: no cover - exercised by tests/data_layer/orm/test_optional_extra.py
    raise ImportError(SQLALCHEMY_MISSING) from error

from sincpro_framework.data_layer.orm.sqlalchemy import (
    Database,
    DatabaseAggregateRepository,
    DatabaseNumbering,
    Isolation,
    JsonText,
    Orphans,
    Relation,
    Repository,
    SqlUseCases,
    TranslatedText,
    Writes,
    invalidate_on_commit,
    map_aggregates,
    map_events,
    register_grain_translator,
    template_table,
)

if TYPE_CHECKING:
    from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.migrations import (
        AlembicEngine,
    )

__all__ = [
    "AlembicEngine",
    "Database",
    "DatabaseAggregateRepository",
    "DatabaseNumbering",
    "Isolation",
    "JsonText",
    "Orphans",
    "Relation",
    "Repository",
    "SqlUseCases",
    "TranslatedText",
    "Writes",
    "invalidate_on_commit",
    "map_aggregates",
    "map_events",
    "register_grain_translator",
    "template_table",
]


def __getattr__(name: str) -> Any:
    """`AlembicEngine` and `version_table` load Alembic only when asked: the `[sqlalchemy]` extra
    never requires the `[migrations]` one."""
    if name in ("AlembicEngine", "version_table"):
        from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint import migrations

        return getattr(migrations, name)
    raise AttributeError(
        f"module 'sincpro_framework.data_layer.orm' has no attribute {name!r}"
    )
