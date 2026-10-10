"""`InMemoryEngine`: a store whose position lives in memory — for tests, and for learning the
orchestrator without a database.

Its step bodies are small text files, so a manifest, its checksums and `check` behave exactly
as they do for a real engine; `applied` and `reverted` record what ran, in order.
"""

import re
from pathlib import Path

from sincpro_framework.data_layer.migrations.domain.engine import MigrationEngine
from sincpro_framework.data_layer.migrations.domain.step import Chain, Position, Step


def _slug(message: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", message.lower()).strip("_")[:40]


class InMemoryEngine(MigrationEngine):
    name = "memory"

    def __init__(self, transactional: bool = True) -> None:
        self.transactional = transactional
        self.applied: list[Step] = []
        self.reverted: list[Step] = []
        self._positions: dict[str, Position] = {}
        self._failing: str | None = None

    def fail_on(self, step_id: str | None) -> None:
        """Make the step with this id fail when it is applied; `None` to stop."""
        self._failing = step_id

    def position(self, chain: Chain) -> Position:
        return self._positions.get(chain.key, Position(None))

    def record(self, chain: Chain, position: Position) -> None:
        self._positions[chain.key] = position

    def apply(self, chain: Chain, step: Step) -> None:
        if step.id == self._failing:
            raise RuntimeError(f"step {step.id} failed")
        self._positions[chain.key] = Position(step.id)
        self.applied.append(step)

    def revert(self, chain: Chain, step: Step) -> None:
        self._positions[chain.key] = Position(step.parent)
        self.reverted.append(step)

    def scaffold(self, chain: Chain, step: Step) -> Path:
        chain.folder.mkdir(parents=True, exist_ok=True)
        body = chain.folder / f"{step.id}_{_slug(step.message)}.txt"
        body.write_text(f"{step.message}\n")
        return body
