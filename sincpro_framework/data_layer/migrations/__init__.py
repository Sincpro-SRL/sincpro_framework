"""Migrations across bounded contexts and stores: one timeline, one command, any engine.

    chat_migrations = ContextMigrations("chat", Path(__file__).parent)
    chat_migrations.store("main", AlembicEngine(tables.metadata, database))
    chat_migrations.store("messages", MongoEngine(client))       # a MigrationEngine of yours

    migrations = Migrations([common_migrations, chat_migrations])
    raise SystemExit(command_line(migrations))                    # status, upgrade, downgrade …

Opt-in, twice: a project may migrate however it likes and never import this; or use the
orchestrator and implement `MigrationEngine` for any store. The core needs no database — the
Alembic engine is `sincpro_framework.data_layer.orm`, behind the `[migrations]` extra.

`domain/` holds the vocabulary, the engine contract, a context's stores and the statuses;
`adapters/` the in-memory engine and a context's `meta_migration.json`; `infrastructure/` the
ids of new steps; `services/` the timeline and the operations; `entrypoint/` what a project
drives: the `Migrations` facade and the command line.
"""

from sincpro_framework.data_layer.migrations.adapters.in_memory import InMemoryEngine
from sincpro_framework.data_layer.migrations.domain.context_migrations import (
    ContextMigrations,
)
from sincpro_framework.data_layer.migrations.domain.engine import MigrationEngine
from sincpro_framework.data_layer.migrations.domain.exceptions import (
    MigrationFailed,
    MigrationRefused,
)
from sincpro_framework.data_layer.migrations.domain.status import (
    ChainStatus,
    MigrationStatus,
)
from sincpro_framework.data_layer.migrations.domain.step import (
    Chain,
    ChainState,
    Position,
    Step,
)
from sincpro_framework.data_layer.migrations.entrypoint.command_line import command_line
from sincpro_framework.data_layer.migrations.entrypoint.orchestrator import Migrations

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
