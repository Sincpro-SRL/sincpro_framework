"""What the core ships behind `MigrationEngine`: an engine in memory. The Alembic engine, for
SQL stores, is in `sincpro_framework.orm.migrations` — it needs the `[migrations]` extra."""

from sincpro_framework.migrations.adapters.in_memory import InMemoryEngine

__all__ = ["InMemoryEngine"]
