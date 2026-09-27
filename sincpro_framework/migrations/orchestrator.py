"""`Migrations`: where the whole system stands, and moving it forward or back as one.

    # entrypoints/migrations.py — the composition root
    migrations = Migrations([common_migrations, billing_migrations, chat_migrations])

    if __name__ == "__main__":
        raise SystemExit(command_line(migrations))

Context: every chain of every context is merged into one timeline; each store answers where it
stands, and the orchestrator compares that with the steps the code knows. It runs with the
system down — `make migrate`, then `make run` — so there are no locks, and it never reverts on
its own: a step that fails stops the run where it is.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from time import perf_counter

from sincpro_log.logger import LoggerProxy, create_logger

from sincpro_framework.migrations.domain import (
    Chain,
    ChainState,
    MigrationEngine,
    MigrationFailed,
    MigrationRefused,
    Position,
    Step,
    new_step_id,
    timeline,
)
from sincpro_framework.migrations.manifest import (
    ALGORITHM,
    MANIFEST,
    Manifest,
    StoreSteps,
    checksum,
    read_manifest,
    sum_of,
    write_manifest,
)
from sincpro_framework.migrations.registry import ContextMigrations

BASE = "base"
"""The target of a downgrade that reverts every step."""


@dataclass(frozen=True)
class ChainStatus:
    chain: Chain
    state: ChainState
    position: Position
    pending: tuple[Step, ...]


@dataclass(frozen=True)
class MigrationStatus:
    chains: dict[str, ChainStatus]
    timeline: list[tuple[Step, bool]]
    """Every step in the order it runs, and whether its store has applied it."""


def _state(steps: Sequence[Step], position: Position) -> ChainState:
    """Context: a head the code does not know is *ahead* — a step of a newer release, or of a
    branch that was abandoned; its id cannot tell which, since a step merged late keeps an old
    one."""
    if position.dirty:
        return ChainState.DIRTY
    ids = [step.id for step in steps]
    if position.head is None:
        return ChainState.BEHIND if ids else ChainState.UP_TO_DATE
    if position.head in ids:
        return ChainState.UP_TO_DATE if position.head == ids[-1] else ChainState.BEHIND
    return ChainState.AHEAD


def _applied(steps: Sequence[Step], position: Position) -> list[Step]:
    ids = [step.id for step in steps]
    if position.head not in ids:
        return []
    return list(steps[: ids.index(position.head) + 1])


def _refusal(status: ChainStatus) -> str:
    key, head = status.chain.key, status.position.head
    if status.state == ChainState.AHEAD:
        return (
            f"{key} is ahead: it stands on {head}, which this code does not have — a step of a "
            "newer release (downgrade with that release first) or of an abandoned branch "
            "(resolve it at a step of this code)"
        )
    chain = status.chain
    at = f" --at {head}" if head else ""
    failed = (
        f"{status.pending[0].id} ({status.pending[0].message})"
        if status.pending
        else "the step after it"
    )
    return (
        f"{key} is dirty: {failed} failed part-way after {head or 'the start'} — clean up what "
        f"it left in the store, then run `resolve {chain.context} {chain.store}{at}` (or --at "
        "the failed step, if it did land whole)"
    )


def _linear_problems(key: str, steps: Sequence[Step]) -> list[str]:
    problems: list[str] = []
    seen: set[str] = set()
    parent: str | None = None
    for step in steps:
        if step.id in seen or step.parent != parent:
            problems.append(
                f"{key} is not linear at {step.id} ({step.message}): its parent is "
                f"{step.parent}, the step before it is {parent} — two branches added a step; "
                "revision one of them again on top of the other"
            )
        seen.add(step.id)
        parent = step.id
    return problems


class Migrations:
    def __init__(self, contexts: Sequence[ContextMigrations]) -> None:
        names = [context.name for context in contexts]
        repeated = sorted({name for name in names if names.count(name) > 1})
        if repeated:
            raise ValueError(f"contexts registered twice: {', '.join(repeated)}")
        folders: dict[Path, str] = {}
        for context in contexts:
            if context.folder.resolve() in folders:
                raise ValueError(
                    f"{folders[context.folder.resolve()]} and {context.name} share one folder: "
                    "each context keeps its own meta_migration.json"
                )
            folders[context.folder.resolve()] = context.name
        self.contexts = {context.name: context for context in contexts}
        self.logger: LoggerProxy = create_logger("migrations")

    def _context(self, name: str) -> ContextMigrations:
        if name not in self.contexts:
            raise MigrationRefused(
                f"no context {name}: registered are {', '.join(self.contexts)}"
            )
        return self.contexts[name]

    def _engine(self, step: Step) -> MigrationEngine:
        return self.contexts[step.context].engines[step.store]

    def _manifest(self, context: ContextMigrations) -> Manifest:
        return read_manifest(context.folder, context.name)

    def _integrity_problems(self) -> list[str]:
        """What the manifests say is wrong with the code itself — no store is read."""
        problems: list[str] = []
        for context in self.contexts.values():
            manifest = self._manifest(context)
            for store in sorted(set(manifest.stores) - set(context.engines)):
                problems.append(
                    f"{context.name}/{store} is in {MANIFEST} but not registered in code"
                )
            for store, entry in manifest.stores.items():
                key = f"{context.name}/{store}"
                engine = context.engines.get(store)
                if engine is not None and engine.name != entry.engine:
                    problems.append(
                        f"{key}: its steps were written for the engine {entry.engine}, and the "
                        f"code registers {engine.name}"
                    )
                problems += _linear_problems(key, entry.steps)
                for step in entry.steps:
                    body = context.folder / store / step.file
                    if not step.checksum.startswith(f"{ALGORITHM}:"):
                        problems.append(
                            f"{key} {step.id}: hashed with an algorithm this framework does not "
                            "know — a newer sincpro-framework wrote it"
                        )
                    elif not body.exists():
                        problems.append(f"{key} {step.id}: {step.file} is missing")
                    elif checksum(body) != step.checksum:
                        problems.append(
                            f"{key} {step.id}: {step.file} changed since it was hashed — review "
                            "it, then run hash"
                        )
            if manifest.stores and manifest.sum != sum_of(manifest):
                problems.append(f"{context.name}: the sum of {MANIFEST} is stale — run hash")
        try:
            timeline([steps for _, steps in self._chains()])
        except ValueError as error:
            problems.append(str(error))
        return problems

    def _refuse_unless_intact(self) -> None:
        problems = self._integrity_problems()
        if problems:
            raise MigrationRefused("; ".join(problems))

    def _chains(self) -> list[tuple[Chain, list[Step]]]:
        chains: list[tuple[Chain, list[Step]]] = []
        for context in self.contexts.values():
            manifest = self._manifest(context)
            for store in context.engines:
                entry = manifest.stores.get(store)
                chains.append((context.chain(store), entry.steps if entry else []))
        return chains

    def _statuses(self, chains: list[tuple[Chain, list[Step]]]) -> dict[str, ChainStatus]:
        statuses: dict[str, ChainStatus] = {}
        for chain, steps in chains:
            engine = self.contexts[chain.context].engines[chain.store]
            position = engine.position(chain)
            applied = {step.id for step in _applied(steps, position)}
            pending = tuple(step for step in steps if step.id not in applied)
            statuses[chain.key] = ChainStatus(
                chain, _state(steps, position), position, pending
            )
        return statuses

    def _refuse_unless_startable(self, statuses: dict[str, ChainStatus]) -> None:
        blocked = [
            status
            for status in statuses.values()
            if status.state in (ChainState.AHEAD, ChainState.DIRTY)
        ]
        if blocked:
            raise MigrationRefused("; ".join(_refusal(status) for status in blocked))

    def _index_of(self, order: list[Step], target: str) -> int:
        """The step whose id is `target` or starts with it — `status` prints the first twelve
        characters."""
        found = [index for index, step in enumerate(order) if step.id.startswith(target)]
        if not found:
            raise MigrationRefused(f"{target} is not the start of any step id of this code")
        if len(found) > 1:
            ids = ", ".join(order[index].id for index in found)
            raise MigrationRefused(f"{target} is the start of several step ids: {ids}")
        return found[0]

    def _run(self, step: Step, forward: bool) -> None:
        """Context: for a store without transactions the position is recorded dirty before the
        step and clean after it — a failure in between leaves the dirty mark for a human.

        1. A non-transactional store: record it dirty where the step starts.
        2. Apply or revert the step; a failure is `MigrationFailed`, naming the step.
        3. Final: record it clean where the step leaves it, and log what ran.
        """
        engine = self._engine(step)
        chain = self.contexts[step.context].chain(step.store)
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
            raise MigrationFailed(
                f"{step.chain_key} {step.id} ({step.message}) failed while being {verb}: {error}"
            ) from error
        if not engine.transactional:
            engine.record(chain, Position(end))
        elapsed = (perf_counter() - started) * 1000
        self.logger.info(f"{verb} {step.key} ({step.message}) in {elapsed:.0f} ms")

    def chain(self, context: str, store: str) -> Chain:
        return self._context(context).chain(store)

    def status(self) -> MigrationStatus:
        chains = self._chains()
        statuses = self._statuses(chains)
        pending = {step.key for status in statuses.values() for step in status.pending}
        try:
            order = timeline([steps for _, steps in chains])
        except ValueError as error:
            raise MigrationRefused(str(error)) from error
        return MigrationStatus(statuses, [(step, step.key not in pending) for step in order])

    def upgrade(self, to: str | None = None) -> list[Step]:
        """Apply every pending step in timeline order — up to `to` when given — and answer what
        ran. Refused, before anything runs, when a body changed since it was hashed, or a store
        is ahead or dirty."""
        self._refuse_unless_intact()
        status = self.status()
        self._refuse_unless_startable(status.chains)
        order = [step for step, _ in status.timeline]
        last = self._index_of(order, to) if to is not None else len(order) - 1
        pending = [step for step, applied in status.timeline[: last + 1] if not applied]
        for step in pending:
            self._run(step, forward=True)
        return pending

    def downgrade(self, to: str) -> list[Step]:
        """Put the whole system back to the step `to` — `"base"` for nothing applied — reverting
        every later applied step, newest first, across every context and store. Refused, before
        anything runs, when one of them is irreversible."""
        self._refuse_unless_intact()
        status = self.status()
        self._refuse_unless_startable(status.chains)
        order = [step for step, _ in status.timeline]
        first = 0 if to == BASE else self._index_of(order, to) + 1
        target, target_applied = status.timeline[first - 1] if first else (None, True)
        if target is not None and not target_applied:
            raise MigrationRefused(
                f"{target.id} ({target.message}) is not applied — the system never stood there"
            )
        to_revert = [step for step, applied in reversed(status.timeline[first:]) if applied]
        irreversible = [step for step in to_revert if step.irreversible]
        if irreversible:
            names = ", ".join(f"{step.key} ({step.message})" for step in irreversible)
            raise MigrationRefused(
                f"{names} is irreversible — the downgrade would cross it; restore a backup "
                "taken before it instead"
            )
        for step in to_revert:
            self._run(step, forward=False)
        return to_revert

    def revision(
        self,
        context: str,
        store: str,
        message: str,
        requires: Sequence[str] = (),
        irreversible: bool = False,
    ) -> Step:
        """A new step at the end of the chain: its engine writes the body, the context's
        manifest records it. `requires` names steps of other chains — `context/store/id`."""
        registry = self._context(context)
        chain = registry.chain(store)
        engine = registry.engines[store]
        known = {step.key for _, steps in self._chains() for step in steps}
        unknown = [key for key in requires if key not in known]
        if unknown:
            raise MigrationRefused(f"requires {', '.join(unknown)}, which no chain has")
        manifest = self._manifest(registry)
        entry = manifest.stores.setdefault(store, StoreSteps(engine.name))
        parent = entry.steps[-1].id if entry.steps else None
        draft = Step(
            new_step_id(),
            context,
            store,
            parent,
            message,
            "",
            "",
            tuple(requires),
            irreversible,
        )
        body = engine.scaffold(chain, draft)
        step = replace(
            draft, file=body.relative_to(chain.folder).as_posix(), checksum=checksum(body)
        )
        entry.steps.append(step)
        write_manifest(registry.folder, manifest)
        self.logger.info(f"revision {step.key} ({message}) at {step.file}")
        return step

    def resolve(self, context: str, store: str, at: str | None) -> None:
        """After a human looked at a dirty store: record the step it really stands on — `None`
        for none."""
        registry = self._context(context)
        chain = registry.chain(store)
        entry = self._manifest(registry).stores.get(store)
        steps = entry.steps if entry else []
        head = steps[self._index_of(steps, at)].id if at is not None else None
        registry.engines[store].record(chain, Position(head))
        self.logger.info(f"resolved {chain.key} at {head}")

    def hash(self) -> list[str]:
        """Checksum every step body again and rewrite each manifest — after writing the body of a
        new step, or reviewing an edit to one its store has not applied. Answers the steps whose
        checksum changed. Refused, before anything is written, when one of them is applied: an
        applied step is never edited — a new step changes what it did."""
        applied = {step.key for step, is_applied in self.status().timeline if is_applied}
        manifests: list[tuple[ContextMigrations, Manifest]] = []
        changed: list[str] = []
        for context in self.contexts.values():
            manifest = self._manifest(context)
            for store, entry in manifest.stores.items():
                hashed = [
                    replace(step, checksum=checksum(context.folder / store / step.file))
                    for step in entry.steps
                ]
                changed += [
                    new.key
                    for old, new in zip(entry.steps, hashed)
                    if old.checksum != new.checksum
                ]
                entry.steps = hashed
            manifests.append((context, manifest))
        edited = [key for key in changed if key in applied]
        if edited:
            raise MigrationRefused(
                f"{', '.join(edited)} changed, and is already applied — revert the edit and write "
                "a new step instead"
            )
        for context, manifest in manifests:
            write_manifest(context.folder, manifest)
        return changed

    def check(self) -> list[str]:
        """What CI refuses, as sentences: a store the code does not register, a chain that is not
        linear, a body edited since it was hashed, a manifest whose sum is stale, a cycle or an
        unknown `requires` — and, for each store that is up to date, what its engine reports as
        drift. Empty when all is well. Where each store stands is `status`, not this: CI upgrades
        a fresh store, then checks."""
        problems = self._integrity_problems()
        for key, status in self.status().chains.items():
            if status.state == ChainState.UP_TO_DATE:
                engine = self.contexts[status.chain.context].engines[status.chain.store]
                problems += [f"{key}: {one}" for one in engine.drift(status.chain) or []]
        return problems
