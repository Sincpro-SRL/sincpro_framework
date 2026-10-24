"""`MigrationEngine`: the one contract a store implements to be migrated by `Migrations`.

    class MongoEngine(MigrationEngine):
        name = "mongo"
        transactional = False          # the orchestrator marks the store dirty around each step
        ...

Context: an abstract class rather than a `Protocol`, like `Repository` — structural typing
checks names, not signatures. The engine owns everything about its store: where it records its
position (in the store itself, next to the data it migrates), how a step's body is written, run
and reverted. The orchestrator owns the order.
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from pathlib import Path

from sincpro_framework.data_layer.migrations.domain.step import Chain, Position, Step


class MigrationEngine(ABC):
    """The contract a store implements to be migrated: it reads and records where the store
    stands, runs and reverts one step, and writes a new step's body.

        class MongoEngine(MigrationEngine):
            name = "mongo"
            transactional = False

            def position(self, chain):        ->  Position read from a collection in the store
            def apply(self, chain, step):     runs `chain.folder / step.file`, records step.id
            def revert(self, chain, step):    undoes it, records step.parent
            def scaffold(self, chain, step):  ->  the path of the new body it wrote

    `InMemoryEngine` is a complete one, small enough to read.
    """

    name: str
    """Written in the manifest next to each store: which engine its step bodies are for."""

    transactional: bool = True
    """`True` when a failed step leaves the store where it was. `False` — a store without
    transactional DDL — and the orchestrator records the store dirty before each step, so a
    failure part-way stops every later run until someone resolves it."""

    @abstractmethod
    def position(self, chain: Chain) -> Position:
        """Where the store stands, read from the store."""

    @abstractmethod
    def record(self, chain: Chain, position: Position) -> None:
        """Set where the store stands — around a non-transactional step, and on `resolve`."""

    @abstractmethod
    def apply(self, chain: Chain, step: Step) -> None:
        """Run the step; on success the store stands on it."""

    @abstractmethod
    def revert(self, chain: Chain, step: Step) -> None:
        """Undo the step; on success the store stands on its parent."""

    @abstractmethod
    def scaffold(self, chain: Chain, step: Step) -> Path:
        """Write the body of a new step under `chain.folder`, and answer its path."""

    def coexist(self, neighbours: Sequence["MigrationEngine"]) -> None:
        """Told, when `Migrations` is built, which other engines migrate beside this one — so it
        can leave alone what they own. Most engines own their store alone and ignore it."""

    def drift(self, chain: Chain) -> list[str] | None:
        """What differs between the store and what the code declares; `None` when this engine
        cannot tell."""
        return None
