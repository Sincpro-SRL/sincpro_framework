"""`ContextMigrations`: what a bounded context migrates — its stores, each with its engine.

    # domains/chat/entrypoints/migrations/__init__.py
    chat_migrations = ContextMigrations("chat", Path(__file__).parent)
    chat_migrations.store("main", AlembicEngine(tables.metadata, database))
    chat_migrations.store("messages", MongoEngine(client))      # an engine the project wrote

Context: the folder holds the context's `meta_migration.json` and one folder of step bodies
per store — `<folder>/<store>/`.
"""

from collections.abc import Mapping
from pathlib import Path

from sincpro_framework.data_layer.migrations.domain.engine import MigrationEngine
from sincpro_framework.data_layer.migrations.domain.step import Chain, MigrationRefused


class ContextMigrations:
    def __init__(self, name: str, folder: Path) -> None:
        self.name = name
        self.folder = folder
        self._engines: dict[str, MigrationEngine] = {}

    def store(self, name: str, engine: MigrationEngine) -> None:
        if name in self._engines:
            raise ValueError(f"'{self.name}' already has a store {name}")
        self._engines[name] = engine

    @property
    def engines(self) -> Mapping[str, MigrationEngine]:
        return self._engines

    def chain(self, store: str) -> Chain:
        if store not in self._engines:
            raise MigrationRefused(
                f"'{self.name}' has no store {store}: its stores are {', '.join(self._engines)}"
            )
        return Chain(self.name, store, self.folder / store)
