"""The SQLAlchemy backend, in the layers every framework component uses. A layer imports only
the ones below it (`tests/data_layer/orm/test_layers.py`):

    entrypoint/       `Repository` — the facade a Feature receives, composed of the workflows
    services/         workflows/ — `UnitOfWork`, `Reading`, `Writing` over the `Store` they share,
                      orchestrating the atomic services: the data mapper, the SQL translator, the
                      relation resolver, the cascade, the upsert, `describe`
    domain/           the adapter's vocabulary, no I/O: `Held`, `Orphans`, the mapping registry,
                      `Isolation`, `Transaction`, the column types
    infrastructure/   `Database` and what every read and write goes through: the engine's errors
                      named, the hooks of a transaction, observability, change and read tracking

The public API is what this package and `sincpro_framework.data_layer.orm` export; the paths inside are the
framework's own. Everything here knows SQLAlchemy; nothing in `sincpro_framework.ddd` does.
"""

from sincpro_framework.data_layer.orm.sqlalchemy.domain.custom_fields import (
    JsonText,
    TranslatedText,
)
from sincpro_framework.data_layer.orm.sqlalchemy.domain.relations import (
    Orphans,
    Relation,
)
from sincpro_framework.data_layer.orm.sqlalchemy.domain.transaction import (
    Isolation,
    Writes,
)
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint import template_table
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.aggregate_repository import (
    DatabaseAggregateRepository,
)
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.mapping import (
    map_aggregates,
    map_events,
    register_grain_translator,
)
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.numbering import (
    DatabaseNumbering,
)
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.repository import (
    Repository,
)
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.runtime_use_cases import (
    SqlUseCases,
)
from sincpro_framework.data_layer.orm.sqlalchemy.infrastructure.cache_invalidation import (
    invalidate_on_commit,
)
from sincpro_framework.data_layer.orm.sqlalchemy.infrastructure.database import (
    Database,
)

__all__ = [
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
