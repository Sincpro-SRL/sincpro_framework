"""The Alembic engine for `sincpro_framework.data_layer.migrations` — SQL stores, behind the
`[migrations]` extra.

    from sincpro_framework.data_layer.orm import AlembicEngine

    billing_migrations.store("main", AlembicEngine(tables.metadata, database))
"""

ALEMBIC_MISSING = (
    "Alembic is not installed. Install with: pip install sincpro-framework[migrations]"
)

try:
    import alembic  # noqa: F401
except (
    ImportError
) as error:  # pragma: no cover - exercised by tests/data_layer/orm/test_optional_extra.py
    raise ImportError(ALEMBIC_MISSING) from error

from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.migrations.engine import (
    AlembicEngine,
    version_table,
)

__all__ = ["AlembicEngine", "version_table"]
