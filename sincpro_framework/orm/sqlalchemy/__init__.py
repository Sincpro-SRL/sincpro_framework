"""The SQLAlchemy backend, in the layers every framework component uses. A layer imports only
the ones below it (`tests/orm/test_layers.py`):

    entrypoint/       `Repository` — the facade a Feature receives, composed of the workflows
    services/         workflows/ — `UnitOfWork`, `Reading`, `Writing` over the `Store` they share,
                      orchestrating the atomic services: the data mapper, the SQL translator, the
                      relation resolver, the cascade, the upsert, `describe`
    domain/           the adapter's vocabulary, no I/O: `Held`, `Orphans`, the mapping registry,
                      `Isolation`, `Transaction`, the column types
    infrastructure/   `Database` and what every read and write goes through: the engine's errors
                      named, the hooks of a transaction, observability, change and read tracking

The public API is what this package and `sincpro_framework.orm` export; the paths inside are the
framework's own. Everything here knows SQLAlchemy; nothing in `sincpro_framework.ddd` does.
"""

from sincpro_framework.orm.sqlalchemy.domain.custom_fields import JsonText, TranslatedText
from sincpro_framework.orm.sqlalchemy.domain.relations import Orphans
from sincpro_framework.orm.sqlalchemy.domain.transaction import Isolation, Writes
from sincpro_framework.orm.sqlalchemy.entrypoint.repository import Repository
from sincpro_framework.orm.sqlalchemy.infrastructure.cache_invalidation import (
    invalidate_on_commit,
)
from sincpro_framework.orm.sqlalchemy.infrastructure.database import Database
from sincpro_framework.orm.sqlalchemy.services.data_mapper import (
    Relation,
    archive_columns,
    audit_columns,
    delivery_columns,
    entity_columns,
    entity_table,
    event_columns,
    event_log_table,
    map_aggregates,
)
from sincpro_framework.orm.sqlalchemy.services.model_introspection import describe
from sincpro_framework.orm.sqlalchemy.services.sql_translator import register_grain_translator
from sincpro_framework.orm.sqlalchemy.services.workflows.reading import Explained

__all__ = [
    "Isolation",
    "Writes",
    "Orphans",
    "Database",
    "Explained",
    "JsonText",
    "Relation",
    "archive_columns",
    "audit_columns",
    "delivery_columns",
    "Repository",
    "TranslatedText",
    "describe",
    "entity_columns",
    "event_columns",
    "event_log_table",
    "entity_table",
    "invalidate_on_commit",
    "map_aggregates",
    "register_grain_translator",
]
