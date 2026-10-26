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

from sincpro_framework.data_layer.migrations.domain import exceptions
from sincpro_framework.data_layer.migrations.domain.engine import MigrationEngine
from sincpro_framework.data_layer.migrations.domain.step import Chain


class ContextMigrations:
    """What one bounded context migrates: a name, the folder of its manifest and step bodies,
    and one engine per store it declares.

        billing = ContextMigrations("billing", Path(__file__).parent)
        billing.store("main", AlembicEngine(tables.metadata, database))
        billing.chain("main")  ->  Chain("billing", "main", <folder>/main)
    """

    def __init__(self, name: str, folder: Path) -> None:
        """`folder` holds one sub-folder per store — its manifest and step bodies."""
        self.name = name
        self.folder = folder
        self._engines: dict[str, MigrationEngine] = {}

    def store(self, name: str, engine: MigrationEngine) -> None:
        """Declares a store of the context and the engine that migrates it; a name declared
        twice is refused as `ValueError`.
        """
        if name in self._engines:
            raise ValueError(f"'{self.name}' already has a store {name}")
        self._engines[name] = engine

    @property
    def engines(self) -> Mapping[str, MigrationEngine]:
        """The context's stores by name, each with its engine — read only."""
        return self._engines

    def chain(self, store: str) -> Chain:
        """The chain of one of the context's stores, its bodies under `<folder>/<store>`; a
        store not declared is refused as `MigrationRefused` naming the declared ones.
        """
        if store not in self._engines:
            raise exceptions.MigrationRefused(
                f"'{self.name}' has no store {store}: its stores are {', '.join(self._engines)}"
            )
        return Chain(self.name, store, self.folder / store)
