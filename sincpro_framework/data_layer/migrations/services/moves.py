"""`Moves`: the system moved forward or back as one, and a store's position recorded by hand.

Context: it runs with the system down — `make migrate`, then `make run` — so there are no locks,
and it never reverts on its own: a step that fails stops the run where it is. A run is refused
before anything executes when the code is not intact or a store is ahead or dirty.
"""

from collections.abc import Sequence
from time import perf_counter

from sincpro_framework.data_layer.migrations.domain import exceptions
from sincpro_framework.data_layer.migrations.domain.status import ChainStatus
from sincpro_framework.data_layer.migrations.domain.step import ChainState, Position, Step
from sincpro_framework.data_layer.migrations.services.system import MigrationSystem

BASE = "base"
"""The target of a downgrade that reverts every step."""


def _index_of(steps: Sequence[Step], target: str) -> int:
    """The step whose id is `target` or starts with it — `status` prints the first twelve
    characters; refused when none or several do.

        steps "0192a1…", "0192b7…";  "0192b"  ->  1
        steps "0192a1…", "0192b7…";  "0192"   ->  MigrationRefused("… several step ids …")
    """
    found = [index for index, step in enumerate(steps) if step.id.startswith(target)]
    if not found:
        raise exceptions.MigrationRefused(
            f"{target} is not the start of any step id of this code"
        )
    if len(found) > 1:
        ids = ", ".join(steps[index].id for index in found)
        raise exceptions.MigrationRefused(f"{target} is the start of several step ids: {ids}")
    return found[0]


def _refuse_unless_startable(statuses: dict[str, ChainStatus]) -> None:
    """Refuses when any store is ahead or dirty, with one sentence per blocked store saying how
    to get it moving. A behind store is what a run is for, so it passes.
    """
    blocked = [
        status
        for status in statuses.values()
        if status.state in (ChainState.AHEAD, ChainState.DIRTY)
    ]
    if blocked:
        raise exceptions.MigrationRefused("; ".join(status.refusal() for status in blocked))


class Moves:
    """Moves every store of the system along the one timeline, and records where a store
    stands when a human says so.

        moves = Moves(system)
        moves.upgrade()                 every pending step, in timeline order
        moves.downgrade("0192a1…")      back to a step — "base" for nothing applied
    """

    def __init__(self, system: MigrationSystem) -> None:
        """The system whose stores it moves."""
        self.system = system

    def _run(self, step: Step, forward: bool) -> None:
        """Context: for a store without transactions the position is recorded dirty before the
        step and clean after it — a failure in between leaves the dirty mark for a human.

        1. A non-transactional store: record it dirty where the step starts.
        2. Apply or revert the step; a failure is `MigrationFailed`, naming the step.
        3. Final: record it clean where the step leaves it, and log what ran.
        """
        engine = self.system.engine(step)
        chain = self.system.contexts[step.context].chain(step.store)
        start, end = (step.parent, step.id) if forward else (step.id, step.parent)
        verb = "applied" if forward else "reverted"
        started = perf_counter()
        if not engine.transactional:
            engine.record(chain, Position(start, dirty=True))
        try:
            if forward:
                engine.apply(chain, step)
            else:
                engine.revert(chain, step)
        except Exception as error:
            raise exceptions.MigrationFailed(
                f"{step.chain_key} {step.id} ({step.message}) failed while being {verb}: {error}"
            ) from error
        if not engine.transactional:
            engine.record(chain, Position(end))
        elapsed = (perf_counter() - started) * 1000
        self.system.logger.info(f"{verb} {step.key} ({step.message}) in {elapsed:.0f} ms")

    def upgrade_plan(self, to: str | None = None) -> list[Step]:
        """What `upgrade(to)` would apply, in order, running nothing.

        1. Refused when the code is not intact, or a store is ahead or dirty.
        2. The timeline is cut after `to` — a step id or its start — or not at all.
        Final: the steps up to the cut that are not applied.
        """
        self.system.refuse_unless_intact()
        status = self.system.status()
        _refuse_unless_startable(status.chains)
        order = [step for step, _ in status.timeline]
        last = _index_of(order, to) if to is not None else len(order) - 1
        return [step for step, applied in status.timeline[: last + 1] if not applied]

    def upgrade(self, to: str | None = None) -> list[Step]:
        """Apply every pending step in timeline order — up to `to` when given — and answer what
        ran. Refused, before anything runs, as `upgrade_plan` says."""
        pending = self.upgrade_plan(to)
        for step in pending:
            self._run(step, forward=True)
        return pending

    def downgrade_plan(self, to: str) -> list[Step]:
        """What `downgrade(to)` would revert, newest first, running nothing.

        1. Refused when the code is not intact, or a store is ahead or dirty.
        2. The timeline is cut after `to` — at its very start for `"base"`.
        3. Refused when the step `to` names is not applied: the system never stood there.
        4. The applied steps after the cut, newest first; the pending ones have nothing to
           revert.
        5. Refused, naming them, when any of those is irreversible.
        Final: those steps, in the order `downgrade` reverts them.
        """
        self.system.refuse_unless_intact()
        status = self.system.status()
        _refuse_unless_startable(status.chains)
        order = [step for step, _ in status.timeline]
        first = 0 if to == BASE else _index_of(order, to) + 1
        target, target_applied = status.timeline[first - 1] if first else (None, True)
        if target is not None and not target_applied:
            raise exceptions.MigrationRefused(
                f"{target.id} ({target.message}) is not applied — the system never stood there"
            )
        to_revert = [step for step, applied in reversed(status.timeline[first:]) if applied]
        irreversible = [step for step in to_revert if step.irreversible]
        if irreversible:
            names = ", ".join(f"{step.key} ({step.message})" for step in irreversible)
            raise exceptions.MigrationRefused(
                f"{names} is irreversible — the downgrade would cross it; restore a backup "
                "taken before it instead"
            )
        return to_revert

    def downgrade(self, to: str) -> list[Step]:
        """Revert every applied step after `to`, newest first, across every context and store,
        and answer what ran. Refused, before anything runs, as `downgrade_plan` says."""
        to_revert = self.downgrade_plan(to)
        for step in to_revert:
            self._run(step, forward=False)
        return to_revert

    def resolve(self, context: str, store: str, at: str | None) -> None:
        """Record the step a dirty store really stands on — `None` for none.

        1. The context and its chain — refused when not registered.
        2. The chain's steps from the context's manifest.
        3. `at` resolved to one of them — refused, recording nothing, when it names none.
        Final: the store recorded clean on that step.
        """
        registry = self.system.context(context)
        chain = registry.chain(store)
        entry = self.system.manifest(registry).stores.get(store)
        steps = entry.steps if entry else []
        head = steps[_index_of(steps, at)].id if at is not None else None
        registry.engines[store].record(chain, Position(head))
        self.system.logger.info(f"resolved {chain.key} at {head}")

    def adopt(self, context: str, store: str) -> Step:
        """Record a store that already holds what the code declares on its chain's last step.

        Context: the chain's steps were written against an empty store (a baseline per context),
        so they never ran on this one; its engine's drift is what proves the store already
        stands where they lead.

        1. Refused when the code is not intact.
        2. The context, the chain and the store's engine — refused when not registered.
        3. Refused when the manifest has no step for this store: a baseline is written first.
        4. Refused when the store already stands on a step, or is dirty.
        5. The engine's drift: refused when it cannot tell (`None`) or reports a difference.
        Final: the store recorded clean on the chain's last step, and that step answered.
        """
        self.system.refuse_unless_intact()
        registry = self.system.context(context)
        chain = registry.chain(store)
        engine = registry.engines[store]
        entry = self.system.manifest(registry).stores.get(store)
        if entry is None or not entry.steps:
            raise exceptions.MigrationRefused(
                f"{chain.key} has no step to adopt the store at — write its baseline first, "
                "with revision against an empty store"
            )
        position = engine.position(chain)
        if position.head is not None or position.dirty:
            raise exceptions.MigrationRefused(
                f"{chain.key} already stands on {position.head or 'the start, dirty'} — adopt "
                "is for a store no step of this code ran on"
            )
        drift = engine.drift(chain)
        last = entry.steps[-1]
        if drift is None:
            raise exceptions.MigrationRefused(
                f"the {engine.name} engine of {chain.key} cannot tell whether the store holds "
                f"what the code declares — look at it, then run `resolve {context} {store} "
                f"--at {last.id}`"
            )
        if drift:
            raise exceptions.MigrationRefused(
                f"{chain.key} does not hold what its steps lead to: {'; '.join(drift)}"
            )
        engine.record(chain, Position(last.id))
        self.system.logger.info(f"adopted {chain.key} at {last.id} ({last.message})")
        return last
