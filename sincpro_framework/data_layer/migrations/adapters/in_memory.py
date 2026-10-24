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
    """A message made a file name: lower case, runs of anything else than letters and digits
    as one `_`, at most 40 characters.

        _slug("Add read_at to Message!")  ->  "add_read_at_to_message"
    """
    return re.sub(r"[^a-z0-9]+", "_", message.lower()).strip("_")[:40]


class InMemoryEngine(MigrationEngine):
    """An engine whose positions live in a dict: nothing is migrated, `applied` and `reverted`
    record what ran, and `fail_on` makes a step raise — so a test exercises the orchestrator's
    order, refusals and dirty marks without a store.

        engine = InMemoryEngine(transactional=False)
        engine.fail_on(step.id)       the next upgrade stops there, the store left dirty
    """

    name = "memory"

    def __init__(self, transactional: bool = True) -> None:
        """`transactional=False` makes the orchestrator mark the store dirty around each step,
        the way it does for a store without transactional DDL.

            engine = InMemoryEngine()
            engine.applied, engine.reverted     ->  [], []
            engine.position(chain)              ->  Position(None)

        Nothing recorded and no step set to fail: every chain starts before its first step.
        """
        self.transactional = transactional
        self.applied: list[Step] = []
        self.reverted: list[Step] = []
        self._positions: dict[str, Position] = {}
        self._failing: str | None = None

    def fail_on(self, step_id: str | None) -> None:
        """Make the step with this id fail when it is applied; `None` to stop."""
        self._failing = step_id

    def position(self, chain: Chain) -> Position:
        """Where the chain stands in the dict — `Position(None)` before anything was
        recorded."""
        return self._positions.get(chain.key, Position(None))

    def record(self, chain: Chain, position: Position) -> None:
        """The position kept in the dict, under the chain's key."""
        self._positions[chain.key] = position

    def apply(self, chain: Chain, step: Step) -> None:
        """Moves the chain to the step and records it in `applied` — or raises `RuntimeError`
        when `fail_on` named it, before anything moves.
        """
        if step.id == self._failing:
            raise RuntimeError(f"step {step.id} failed")
        self._positions[chain.key] = Position(step.id)
        self.applied.append(step)

    def revert(self, chain: Chain, step: Step) -> None:
        """Moves the chain back to the step's parent and records it in `reverted`; it never
        fails."""
        self._positions[chain.key] = Position(step.parent)
        self.reverted.append(step)

    def scaffold(self, chain: Chain, step: Step) -> Path:
        """A one-line text body — the step's message — at `<chain folder>/<id>_<slug>.txt`,
        the folder made when missing, so manifests and checksums behave as with a real engine.
        """
        chain.folder.mkdir(parents=True, exist_ok=True)
        body = chain.folder / f"{step.id}_{_slug(step.message)}.txt"
        body.write_text(f"{step.message}\n")
        return body
