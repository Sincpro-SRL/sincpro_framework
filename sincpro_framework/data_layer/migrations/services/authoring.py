"""`Authoring`: what writes the manifests — a new step at the end of a chain, and every
checksum taken again."""

from collections.abc import Sequence
from dataclasses import replace

from sincpro_framework.data_layer.migrations.adapters.manifest import Manifest, StoreSteps
from sincpro_framework.data_layer.migrations.domain import exceptions
from sincpro_framework.data_layer.migrations.domain.context_migrations import (
    ContextMigrations,
)
from sincpro_framework.data_layer.migrations.domain.step import Step
from sincpro_framework.data_layer.migrations.infrastructure.step_ids import StepIds
from sincpro_framework.data_layer.migrations.services.system import MigrationSystem


class Authoring:
    """Writes steps into the system's manifests: `revision` adds one, `hash` checksums them
    all again.

        authoring = Authoring(system)
        authoring.revision("chat", "main", "add read_at")   ->  Step(id="0192…c2", ...)

    Context: it owns the `StepIds` its new steps take their ids from, so the steps it writes
    sort in the order it wrote them.
    """

    def __init__(self, system: MigrationSystem) -> None:
        """The system whose manifests it writes, and a fresh id generator."""
        self.system = system
        self.step_ids = StepIds()

    def revision(
        self,
        context: str,
        store: str,
        message: str,
        requires: Sequence[str] = (),
        irreversible: bool = False,
    ) -> Step:
        """A new step at the end of the chain: its engine writes the body, the context's
        manifest records it.

        1. The context, its chain and the store's engine — refused when not registered.
        2. Each key in `requires` must be a step some chain already has.
        3. The context's manifest read fresh — the store's entry created when it has none.
        4. A draft step: a new UUIDv7 id, the last step of the chain as its parent.
        5. The engine writes the body (`scaffold`); the step takes the body's path, relative
           to the chain's folder, and its checksum.
        Final: the step appended to the manifest, the manifest written, the step answered.
        """
        registry = self.system.context(context)
        chain = registry.chain(store)
        engine = registry.engines[store]
        known = {step.key for _, steps in self.system.chains() for step in steps}
        unknown = [key for key in requires if key not in known]
        if unknown:
            raise exceptions.MigrationRefused(
                f"requires {', '.join(unknown)}, which no chain has"
            )
        manifest = self.system.manifest(registry)
        entry = manifest.stores.setdefault(store, StoreSteps(engine.name))
        parent = entry.steps[-1].id if entry.steps else None
        draft = Step(
            self.step_ids.new(),
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
            draft,
            file=body.relative_to(chain.folder).as_posix(),
            checksum=Manifest.checksum(body),
        )
        entry.steps.append(step)
        manifest.write(registry.folder)
        self.system.logger.info(f"revision {step.key} ({message}) at {step.file}")
        return step

    def hash(self) -> list[str]:
        """Checksum every step body again and rewrite each manifest, answering the steps whose
        body changed.

        1. The steps applied anywhere, from `status`.
        2. For every context's manifest, read fresh, and each of its stores:
           2.1 a step whose body no longer matches its checksum is one that changed;
           2.2 every step takes the checksum of its body now, with the current algorithm.
        3. Refused, writing nothing, when a step that changed is applied.
        Final: every manifest written — its `sum` recomputed — and the changed steps answered.
        """
        applied = {
            step.key for step, is_applied in self.system.status().timeline if is_applied
        }
        manifests: list[tuple[ContextMigrations, Manifest]] = []
        changed: list[str] = []
        for context in self.system.contexts.values():
            manifest = self.system.manifest(context)
            for store, entry in manifest.stores.items():
                bodies = [context.folder / store / step.file for step in entry.steps]
                changed += [
                    step.key
                    for step, body in zip(entry.steps, bodies)
                    if not Manifest.matches(body, step.checksum)
                ]
                entry.steps = [
                    replace(step, checksum=Manifest.checksum(body))
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
            manifest.write(context.folder)
        return changed
