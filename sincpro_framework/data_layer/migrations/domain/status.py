"""Where a chain stands, and where the whole system stands — what `status` answers."""

from collections.abc import Sequence
from dataclasses import dataclass

from sincpro_framework.data_layer.migrations.domain.step import (
    Chain,
    ChainState,
    Position,
    Step,
)


@dataclass(frozen=True)
class ChainStatus:
    """Where one chain stands: its store's position, the state that position means against the
    steps the code knows, and the steps it has not applied yet.

        ChainStatus(chain=billing/main, state=BEHIND, position=Position("0192…a1"),
                    pending=(Step("0192…b7", message="add due_date"),))
    """

    chain: Chain
    state: ChainState
    position: Position
    pending: tuple[Step, ...]

    @classmethod
    def of(cls, chain: Chain, steps: Sequence[Step], position: Position) -> "ChainStatus":
        """The status of a chain whose store stands on `position`, against the steps the code
        knows for it.

            steps a, b, c;  Position("a")   ->  ChainStatus(state=BEHIND, pending=(b, c))
            steps a, b, c;  Position("z")   ->  ChainStatus(state=AHEAD, pending=(a, b, c))

        1. The applied steps: every step up to and including the head — none when the head is
           `None` or a step the code does not know.
        Final: the state the position means (`ChainState.of`), and every other step pending.
        """
        ids = [step.id for step in steps]
        applied = set(ids[: ids.index(position.head) + 1]) if position.head in ids else set()
        pending = tuple(step for step in steps if step.id not in applied)
        return cls(chain, ChainState.of(steps, position), position, pending)

    def refusal(self) -> str:
        """The sentence that tells a human why this chain blocks a run and what to do — for an
        *ahead* store, which release or branch to go through; for a *dirty* one, which step
        failed part-way and the exact `resolve` command to run once the store is cleaned up.
        """
        key, head = self.chain.key, self.position.head
        if self.state == ChainState.AHEAD:
            return (
                f"{key} is ahead: it stands on {head}, which this code does not have — a step "
                "of a newer release (downgrade with that release first) or of an abandoned "
                "branch (resolve it at a step of this code)"
            )
        at = f" --at {head}" if head else ""
        failed = (
            f"{self.pending[0].id} ({self.pending[0].message})"
            if self.pending
            else "the step after it"
        )
        return (
            f"{key} is dirty: {failed} failed part-way after {head or 'the start'} — clean up "
            f"what it left in the store, then run `resolve {self.chain.context} "
            f"{self.chain.store}{at}` (or --at the failed step, if it did land whole)"
        )


@dataclass(frozen=True)
class MigrationStatus:
    """Where the whole system stands: every chain by its key (`context/store`), and the one
    timeline every `upgrade` and `downgrade` walks.
    """

    chains: dict[str, ChainStatus]
    timeline: list[tuple[Step, bool]]
    """Every step in the order it runs, and whether its store has applied it."""
