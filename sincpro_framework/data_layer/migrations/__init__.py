"""Migrations across bounded contexts and stores: one timeline, one command, any engine.

    chat_migrations = ContextMigrations("chat", Path(__file__).parent)
    chat_migrations.store("main", AlembicEngine(tables.metadata, database))
    chat_migrations.store("messages", MongoEngine(client))       # a MigrationEngine of yours

    migrations = Migrations([common_migrations, chat_migrations])
    raise SystemExit(command_line(migrations))                    # status, upgrade, downgrade …

Opt-in, twice: a project may migrate however it likes and never import this; or use the
orchestrator and implement `MigrationEngine` for any store. The core needs no database — the
Alembic engine is `sincpro_framework.data_layer.orm`, behind the `[migrations]` extra.

`domain/` holds the vocabulary and the engine contract, `adapters/` the in-memory engine and
a context's `meta_migration.json`, `entrypoint/` what a project drives: a context's stores, the
timeline and its commands, the command line.
"""

from sincpro_framework.data_layer.migrations.adapters.in_memory import InMemoryEngine
from sincpro_framework.data_layer.migrations.domain.engine import MigrationEngine
from sincpro_framework.data_layer.migrations.domain.step import (
    Chain,
    ChainState,
    MigrationFailed,
    MigrationRefused,
    Position,
    Step,
)
from sincpro_framework.data_layer.migrations.entrypoint.command_line import command_line
from sincpro_framework.data_layer.migrations.entrypoint.orchestrator import (
    ChainStatus,
    Migrations,
    MigrationStatus,
)
from sincpro_framework.data_layer.migrations.entrypoint.registry import ContextMigrations

__all__ = [
    "Chain",
    "ChainState",
    "ChainStatus",
    "ContextMigrations",
    "InMemoryEngine",
    "MigrationEngine",
    "MigrationFailed",
    "MigrationRefused",
    "MigrationStatus",
    "Migrations",
    "Position",
    "Step",
    "command_line",
]
