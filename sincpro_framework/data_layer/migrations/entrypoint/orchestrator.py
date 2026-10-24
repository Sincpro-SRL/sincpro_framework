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

from sincpro_framework.data_layer.migrations.adapters.manifest import (
    DIGESTS,
    MANIFEST,
    Manifest,
    StoreSteps,
    checksum,
    matches,
    read_manifest,
    sum_of,
    write_manifest,
)
from sincpro_framework.data_layer.migrations.domain import exceptions
from sincpro_framework.data_layer.migrations.domain.engine import MigrationEngine
from sincpro_framework.data_layer.migrations.domain.step import (
    Chain,
    ChainState,
    Position,
    Step,
)
from sincpro_framework.data_layer.migrations.domain.timeline import new_step_id, timeline
from sincpro_framework.data_layer.migrations.entrypoint.registry import ContextMigrations

BASE = "base"
"""The target of a downgrade that reverts every step."""


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


@dataclass(frozen=True)
class MigrationStatus:
    """Where the whole system stands: every chain by its key (`context/store`), and the one
    timeline every `upgrade` and `downgrade` walks.
    """

    chains: dict[str, ChainStatus]
    timeline: list[tuple[Step, bool]]
    """Every step in the order it runs, and whether its store has applied it."""


def _state(steps: Sequence[Step], position: Position) -> ChainState:
    """The state a store's position means against the steps its chain knows.

    Context: a head the code does not know is *ahead* — a step of a newer release, or of a
    branch that was abandoned; its id cannot tell which, since a step merged late keeps an old
    one.

        steps a, b, c;  Position("c")             ->  UP_TO_DATE
        steps a, b, c;  Position("a")             ->  BEHIND
        steps a, b, c;  Position(None)            ->  BEHIND   (UP_TO_DATE with no steps)
        steps a, b, c;  Position("z")             ->  AHEAD
        any steps;      Position("b", dirty=True) ->  DIRTY
    """
    if position.dirty:
        return ChainState.DIRTY
    ids = [step.id for step in steps]
    if position.head is None:
        return ChainState.BEHIND if ids else ChainState.UP_TO_DATE
    if position.head in ids:
        return ChainState.UP_TO_DATE if position.head == ids[-1] else ChainState.BEHIND
    return ChainState.AHEAD


def _applied(steps: Sequence[Step], position: Position) -> list[Step]:
    """The steps of a chain its store has applied — every step up to and including its head;
    none when the head is `None` or a step the code does not know.
    """
    ids = [step.id for step in steps]
    if position.head not in ids:
        return []
    return list(steps[: ids.index(position.head) + 1])


def _refusal(status: ChainStatus) -> str:
    """The sentence that tells a human why a chain blocks a run and what to do — for an
    *ahead* store, which release or branch to go through; for a *dirty* one, which step failed
    part-way and the exact `resolve` command to run once the store is cleaned up.
    """
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
    """Where a chain stops being one line: a step whose `parent` is not the step before it, or
    an id that repeats.

    Context: two branches that each add a step on the same head merge into a manifest with two
    children of one parent; run in that order, the second would land on a store its body
    was not written against.

        steps a(parent None), b(parent a), c(parent a)
        ->  ["billing/main is not linear at c (…): its parent is a, the step before it is b…"]
    """
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
    """The orchestrator: every registered context's chains as one timeline, read, checked and
    moved forward or back as one.

        migrations.status()                 where each store stands, and the timeline
        migrations.upgrade()                every pending step, in timeline order
        migrations.downgrade("0192a1…")     back to a step — "base" for nothing applied
        migrations.revision("chat", "main", "add read_at")     a new step and its body
        migrations.check()                  what CI refuses, as sentences

    Context: it reads where each store stands from its engine and what the code declares from
    each context's manifest; a run is refused before anything executes when the code is not
    intact (`_refuse_unless_intact`) or a store is ahead or dirty
    (`_refuse_unless_startable`).
    """

    def __init__(self, contexts: Sequence[ContextMigrations]) -> None:
        """The contexts the system migrates, checked and introduced to each other.

            Migrations([common_migrations, billing_migrations])
                ->  migrations.contexts == {"common": common_migrations, "billing": ...}
            Migrations([billing_migrations, billing_migrations])
                ->  ValueError("contexts registered twice: billing")

        1. A context name registered twice is refused.
        2. Two contexts sharing one folder are refused — each keeps its own
           `meta_migration.json`.
        Final: every engine is told which others migrate beside it (`coexist`), so it can
        leave alone what they own — two Alembic engines over one database, for instance.
        """
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
        everyone = [engine for context in contexts for engine in context.engines.values()]
        for engine in everyone:
            engine.coexist([one for one in everyone if one is not engine])

    def _context(self, name: str) -> ContextMigrations:
        """The registered context by its name, refused as `MigrationRefused` naming the
        registered ones when there is none — what a typo on the command line answers.
        """
        if name not in self.contexts:
            raise exceptions.MigrationRefused(
                f"no context {name}: registered are {', '.join(self.contexts)}"
            )
        return self.contexts[name]

    def _engine(self, step: Step) -> MigrationEngine:
        """The engine of the store a step belongs to."""
        return self.contexts[step.context].engines[step.store]

    def _manifest(self, context: ContextMigrations) -> Manifest:
        """A context's manifest, read fresh from its folder each time — the files are the
        truth, and `revision` / `hash` rewrite them between calls.
        """
        return read_manifest(context.folder, context.name)

    def _chains(self) -> list[tuple[Chain, list[Step]]]:
        """Every chain of every context — each registered store with the steps its manifest
        lists, an empty chain for a store that has no step yet.
        """
        chains: list[tuple[Chain, list[Step]]] = []
        for context in self.contexts.values():
            manifest = self._manifest(context)
            for store in context.engines:
                entry = manifest.stores.get(store)
                chains.append((context.chain(store), entry.steps if entry else []))
        return chains

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
                    if step.checksum.partition(":")[0] not in DIGESTS:
                        problems.append(
                            f"{key} {step.id}: hashed with an algorithm this framework does not "
                            "know — a newer sincpro-framework wrote it"
                        )
                    elif not body.exists():
                        problems.append(f"{key} {step.id}: {step.file} is missing")
                    elif not matches(body, step.checksum):
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
        """Refuses with every integrity problem at once — the code itself is wrong, so no
        store is touched.
        """
        problems = self._integrity_problems()
        if problems:
            raise exceptions.MigrationRefused("; ".join(problems))

    def _statuses(self, chains: list[tuple[Chain, list[Step]]]) -> dict[str, ChainStatus]:
        """Each chain's status, asking each engine where its store stands — the only place a
        store is read.
        """
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
        """Refuses when any store is ahead or dirty, with one sentence per blocked store
        saying how to get it moving. A behind store is what a run is for, so it passes.
        """
        blocked = [
            status
            for status in statuses.values()
            if status.state in (ChainState.AHEAD, ChainState.DIRTY)
        ]
        if blocked:
            raise exceptions.MigrationRefused(
                "; ".join(_refusal(status) for status in blocked)
            )

    def _index_of(self, order: list[Step], target: str) -> int:
        """The step whose id is `target` or starts with it — `status` prints the first twelve
        characters."""
        found = [index for index, step in enumerate(order) if step.id.startswith(target)]
        if not found:
            raise exceptions.MigrationRefused(
                f"{target} is not the start of any step id of this code"
            )
        if len(found) > 1:
            ids = ", ".join(order[index].id for index in found)
            raise exceptions.MigrationRefused(
                f"{target} is the start of several step ids: {ids}"
            )
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
            raise exceptions.MigrationFailed(
                f"{step.chain_key} {step.id} ({step.message}) failed while being {verb}: {error}"
            ) from error
        if not engine.transactional:
            engine.record(chain, Position(end))
        elapsed = (perf_counter() - started) * 1000
        self.logger.info(f"{verb} {step.key} ({step.message}) in {elapsed:.0f} ms")

    def chain(self, context: str, store: str) -> Chain:
        """The chain of one store of one context, refused when either is not registered."""
        return self._context(context).chain(store)

    def status(self) -> MigrationStatus:
        """Where the whole system stands, reading every store once; nothing is refused but a
        timeline that cannot be built (a cycle, an unknown `requires`).

            migrations.status().timeline
            ->  [(Step("…a1", "baseline"), True), (Step("…b7", "add due_date"), False)]
        """
        chains = self._chains()
        statuses = self._statuses(chains)
        pending = {step.key for status in statuses.values() for step in status.pending}
        try:
            order = timeline([steps for _, steps in chains])
        except ValueError as error:
            raise exceptions.MigrationRefused(str(error)) from error
        return MigrationStatus(statuses, [(step, step.key not in pending) for step in order])

    def upgrade_plan(self, to: str | None = None) -> list[Step]:
        """What `upgrade(to)` would apply, in order, running nothing — refused as it would be:
        when a body changed since it was hashed, or a store is ahead or dirty.

            migrations.upgrade_plan()            ->  [Step("…b7", "add due_date"),
                                                      Step("…c2", "add read_at")]
            migrations.upgrade_plan(to="…b7")    ->  [Step("…b7", "add due_date")]

        `to` is a step id or its start; the steps after it in the timeline are left out.
        """
        self._refuse_unless_intact()
        status = self.status()
        self._refuse_unless_startable(status.chains)
        order = [step for step, _ in status.timeline]
        last = self._index_of(order, to) if to is not None else len(order) - 1
        return [step for step, applied in status.timeline[: last + 1] if not applied]

    def upgrade(self, to: str | None = None) -> list[Step]:
        """Apply every pending step in timeline order — up to `to` when given — and answer what
        ran. Refused, before anything runs, as `upgrade_plan` says."""
        pending = self.upgrade_plan(to)
        for step in pending:
            self._run(step, forward=True)
        return pending

    def downgrade_plan(self, to: str) -> list[Step]:
        """What `downgrade(to)` would revert, newest first, running nothing — refused as it
        would be: when one of them is irreversible, or the system never stood on `to`.

            timeline: …a1 baseline (applied), …b7 add due_date (applied), …c2 add read_at
            migrations.downgrade_plan("…a1")   ->  [Step("…b7", "add due_date")]
            migrations.downgrade_plan("base")  ->  [Step("…b7", ...), Step("…a1", ...)]

        1. Refused when the code is not intact, or a store is ahead or dirty.
        2. The timeline is cut after `to` — at its very start for `"base"`.
        3. Refused when the step `to` names is not applied: the system never stood there.
        4. The applied steps after the cut, newest first; the pending ones have nothing to
           revert.
        5. Refused, naming them, when any of those is irreversible.
        Final: those steps, in the order `downgrade` reverts them.
        """
        self._refuse_unless_intact()
        status = self.status()
        self._refuse_unless_startable(status.chains)
        order = [step for step, _ in status.timeline]
        first = 0 if to == BASE else self._index_of(order, to) + 1
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
        """Put the whole system back to the step `to` — `"base"` for nothing applied — reverting
        every later applied step, newest first, across every context and store. Refused, before
        anything runs, as `downgrade_plan` says."""
        to_revert = self.downgrade_plan(to)
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
        manifest records it. `requires` names steps of other chains — `context/store/id`.

            migrations.revision("chat", "main", "add read_at")
                ->  Step(id="0192…c2", parent="0192…b7", message="add read_at",
                         file="0192…c2_add_read_at.py", checksum="v2:…")
            migrations.revision("billing", "main", "x", requires=["common/main/nope"])
                ->  MigrationRefused("requires common/main/nope, which no chain has")

        1. The context, its chain and the store's engine — refused when not registered.
        2. Each key in `requires` must be a step some chain already has.
        3. The context's manifest read fresh — the store's entry created when it has none.
        4. A draft step: a new UUIDv7 id, the last step of the chain as its parent.
        5. The engine writes the body (`scaffold`); the step takes the body's path, relative
           to the chain's folder, and its checksum.
        Final: the step appended to the manifest, the manifest written, the step answered.
        """
        registry = self._context(context)
        chain = registry.chain(store)
        engine = registry.engines[store]
        known = {step.key for _, steps in self._chains() for step in steps}
        unknown = [key for key in requires if key not in known]
        if unknown:
            raise exceptions.MigrationRefused(
                f"requires {', '.join(unknown)}, which no chain has"
            )
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
        for none.

            migrations.resolve("billing", "main", "0192b7")   ->  None; clean on …b7
            migrations.resolve("billing", "main", None)       ->  None; clean, on no step

        `at` is a step id of this chain or its start; one that is neither is refused, and
        nothing is recorded.
        """
        registry = self._context(context)
        chain = registry.chain(store)
        entry = self._manifest(registry).stores.get(store)
        steps = entry.steps if entry else []
        head = steps[self._index_of(steps, at)].id if at is not None else None
        registry.engines[store].record(chain, Position(head))
        self.logger.info(f"resolved {chain.key} at {head}")

    def adopt(self, context: str, store: str) -> Step:
        """Record a store that already holds what the code declares — a database another tool
        migrated — on its chain's last step, and answer that step.

        Context: the chain's steps were written against an empty store (a baseline per context),
        so they never ran on this one; its engine's drift is what proves the store already
        stands where they lead. Refused, recording nothing, when the store already stands on a
        step, when its engine cannot tell drift — `resolve --at` is the manual form — or when it
        reports any.

            migrations.adopt("billing", "main")   ->  Step("0192…b7", message="add due_date")
            on a store missing a column:
                ->  MigrationRefused("billing/main does not hold what its steps lead to:
                    column invoice.due_date is declared but no step adds it")

        1. Refused when the code is not intact.
        2. The context, the chain and the store's engine — refused when not registered.
        3. Refused when the manifest has no step for this store: a baseline is written first.
        4. Refused when the store already stands on a step, or is dirty.
        5. The engine's drift: refused when it cannot tell (`None`) or reports a difference.
        Final: the store recorded clean on the chain's last step, and that step answered.
        """
        self._refuse_unless_intact()
        registry = self._context(context)
        chain = registry.chain(store)
        engine = registry.engines[store]
        entry = self._manifest(registry).stores.get(store)
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
        self.logger.info(f"adopted {chain.key} at {last.id} ({last.message})")
        return last

    def hash(self) -> list[str]:
        """Checksum every step body again and rewrite each manifest — after writing the body of a
        new step, or reviewing an edit to one its store has not applied. Answers the steps whose
        body changed. Refused, before anything is written, when one of them is applied: an
        applied step is never edited — a new step changes what it did. A checksum of an older
        algorithm whose body did not change is rewritten with the current one, applied or not.

            migrations.hash()   ->  ["chat/main/0192…c2"]   its body edited before any upgrade
            migrations.hash()   ->  []                      every checksum already matched
            migrations.hash()   ->  MigrationRefused("billing/main/0192…a1 changed, and is
                                    already applied — revert the edit and write a new step …")

        1. The steps applied anywhere, from `status`.
        2. For every context's manifest, read fresh, and each of its stores:
           2.1 a step whose body no longer matches its checksum is one that changed;
           2.2 every step takes the checksum of its body now, with the current algorithm.
        3. Refused, writing nothing, when a step that changed is applied.
        Final: every manifest written — its `sum` recomputed — and the changed steps answered.
        """
        applied = {step.key for step, is_applied in self.status().timeline if is_applied}
        manifests: list[tuple[ContextMigrations, Manifest]] = []
        changed: list[str] = []
        for context in self.contexts.values():
            manifest = self._manifest(context)
            for store, entry in manifest.stores.items():
                bodies = [context.folder / store / step.file for step in entry.steps]
                changed += [
                    step.key
                    for step, body in zip(entry.steps, bodies)
                    if not matches(body, step.checksum)
                ]
                entry.steps = [
                    replace(step, checksum=checksum(body))
                    for step, body in zip(entry.steps, bodies)
                ]
            manifests.append((context, manifest))
        edited = [key for key in changed if key in applied]
        if edited:
            raise exceptions.MigrationRefused(
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
        a fresh store, then checks.

            migrations.check()   ->  []
            migrations.check()
                ->  ["chat: the sum of meta_migration.json is stale — run hash",
                     "billing/main: column invoice.due_date is declared but no step adds it"]

        A store that is behind, ahead or dirty is not asked for drift: its tables are not yet
        where its steps lead.
        """
        problems = self._integrity_problems()
        for key, status in self.status().chains.items():
            if status.state == ChainState.UP_TO_DATE:
                engine = self.contexts[status.chain.context].engines[status.chain.store]
                problems += [f"{key}: {one}" for one in engine.drift(status.chain) or []]
        return problems
