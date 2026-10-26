"""`MigrationSystem`: the registered contexts, read and checked — what the code declares and
where each store stands; it moves nothing and writes nothing.

Context: what the code declares is read from each context's manifest, fresh on every call — the
files are the truth, and `revision` / `hash` rewrite them between calls; where a store stands is
asked of its engine.
"""

from collections.abc import Sequence
from pathlib import Path

from sincpro_log.logger import LoggerProxy, create_logger

from sincpro_framework.data_layer.migrations.adapters.manifest import (
    DIGESTS,
    MANIFEST,
    Manifest,
)
from sincpro_framework.data_layer.migrations.domain import exceptions
from sincpro_framework.data_layer.migrations.domain.context_migrations import (
    ContextMigrations,
)
from sincpro_framework.data_layer.migrations.domain.engine import MigrationEngine
from sincpro_framework.data_layer.migrations.domain.status import (
    ChainStatus,
    MigrationStatus,
)
from sincpro_framework.data_layer.migrations.domain.step import Chain, ChainState, Step
from sincpro_framework.data_layer.migrations.services.timeline import timeline


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


class MigrationSystem:
    """Every registered context's chains: looked up, read from the manifests and the stores,
    and checked — the part `Moves` and `Authoring` share.

        system = MigrationSystem([common_migrations, billing_migrations])
        system.status()                 where each store stands, and the timeline
        system.check()                  what CI refuses, as sentences
    """

    def __init__(self, contexts: Sequence[ContextMigrations]) -> None:
        """The contexts the system migrates, checked and introduced to each other.

            MigrationSystem([common_migrations, billing_migrations])
                ->  system.contexts == {"common": common_migrations, "billing": ...}
            MigrationSystem([billing_migrations, billing_migrations])
                ->  ValueError("contexts registered twice: billing")

        1. A context name registered twice is refused.
        2. Two contexts sharing one folder are refused — each has its own
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
                    "each context has its own meta_migration.json"
                )
            folders[context.folder.resolve()] = context.name
        self.contexts = {context.name: context for context in contexts}
        self.logger: LoggerProxy = create_logger("migrations")
        everyone = [engine for context in contexts for engine in context.engines.values()]
        for engine in everyone:
            engine.coexist([one for one in everyone if one is not engine])

    def context(self, name: str) -> ContextMigrations:
        """The registered context by its name, refused as `MigrationRefused` naming the
        registered ones when there is none — what a typo on the command line answers.
        """
        if name not in self.contexts:
            raise exceptions.MigrationRefused(
                f"no context {name}: registered are {', '.join(self.contexts)}"
            )
        return self.contexts[name]

    def engine(self, step: Step) -> MigrationEngine:
        """The engine of the store a step belongs to."""
        return self.contexts[step.context].engines[step.store]

    def chain(self, context: str, store: str) -> Chain:
        """The chain of one store of one context, refused when either is not registered."""
        return self.context(context).chain(store)

    def manifest(self, context: ContextMigrations) -> Manifest:
        """A context's manifest, read fresh from its folder each time — the files are the
        truth, and `revision` / `hash` rewrite them between calls.
        """
        return Manifest.read(context.folder, context.name)

    def chains(self) -> list[tuple[Chain, list[Step]]]:
        """Every chain of every context — each registered store with the steps its manifest
        lists, an empty chain for a store that has no step yet.
        """
        chains: list[tuple[Chain, list[Step]]] = []
        for context in self.contexts.values():
            manifest = self.manifest(context)
            for store in context.engines:
                entry = manifest.stores.get(store)
                chains.append((context.chain(store), entry.steps if entry else []))
        return chains

    def integrity_problems(self) -> list[str]:
        """What the manifests say is wrong with the code itself — no store is read.

        1. For every context's manifest: a store it lists that the code does not register.
        2. For each of its stores: an engine other than the one its steps were written for, a
           chain that is not linear, and per step a checksum of an unknown algorithm, a missing
           body or a body changed since it was hashed.
        3. A stale `sum`.
        Final: a timeline that cannot be built — a cycle, an unknown `requires`.
        """
        problems: list[str] = []
        for context in self.contexts.values():
            manifest = self.manifest(context)
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
                    elif not Manifest.matches(body, step.checksum):
                        problems.append(
                            f"{key} {step.id}: {step.file} changed since it was hashed — review "
                            "it, then run hash"
                        )
            if manifest.stores and manifest.sum != manifest.computed_sum():
                problems.append(f"{context.name}: the sum of {MANIFEST} is stale — run hash")
        try:
            timeline([steps for _, steps in self.chains()])
        except ValueError as error:
            problems.append(str(error))
        return problems

    def refuse_unless_intact(self) -> None:
        """Refuses with every integrity problem at once — the code itself is wrong, so no
        store is touched.
        """
        problems = self.integrity_problems()
        if problems:
            raise exceptions.MigrationRefused("; ".join(problems))

    def status(self) -> MigrationStatus:
        """Where the whole system stands, asking each engine once where its store stands — the
        only place a store's position is read for a status; nothing is refused but a timeline
        that cannot be built (a cycle, an unknown `requires`).

        1. Every chain, and each one's status from its engine's position.
        2. The keys of every pending step.
        Final: the timeline, each step marked applied unless it is pending.
        """
        chains = self.chains()
        statuses = {
            chain.key: ChainStatus.of(
                chain,
                steps,
                self.contexts[chain.context].engines[chain.store].position(chain),
            )
            for chain, steps in chains
        }
        pending = {step.key for status in statuses.values() for step in status.pending}
        try:
            order = timeline([steps for _, steps in chains])
        except ValueError as error:
            raise exceptions.MigrationRefused(str(error)) from error
        return MigrationStatus(statuses, [(step, step.key not in pending) for step in order])

    def check(self) -> list[str]:
        """What CI refuses: every integrity problem, then the drift each up-to-date store's
        engine reports — a store behind, ahead or dirty is not asked, its tables are not yet
        where its steps lead.
        """
        problems = self.integrity_problems()
        for key, status in self.status().chains.items():
            if status.state == ChainState.UP_TO_DATE:
                engine = self.contexts[status.chain.context].engines[status.chain.store]
                problems += [f"{key}: {one}" for one in engine.drift(status.chain) or []]
        return problems
