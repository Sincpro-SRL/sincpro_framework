"""`Migrations`: where the whole system stands, and moving it forward or back as one.

    # entrypoints/migrations.py — the composition root
    migrations = Migrations([common_migrations, billing_migrations, chat_migrations])

    if __name__ == "__main__":
        raise SystemExit(command_line(migrations))

Context: the facade a project drives. It groups the services — `MigrationSystem` reads and
checks, `Moves` runs steps forward or back, `Authoring` writes new steps and checksums — and
delegates every call to them.
"""

from collections.abc import Mapping, Sequence

from sincpro_framework.data_layer.migrations.domain.context_migrations import (
    ContextMigrations,
)
from sincpro_framework.data_layer.migrations.domain.status import MigrationStatus
from sincpro_framework.data_layer.migrations.domain.step import Chain, Step
from sincpro_framework.data_layer.migrations.services.authoring import Authoring
from sincpro_framework.data_layer.migrations.services.moves import Moves
from sincpro_framework.data_layer.migrations.services.system import MigrationSystem


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
    intact or a store is ahead or dirty.
    """

    def __init__(self, contexts: Sequence[ContextMigrations]) -> None:
        """The contexts the system migrates, checked and introduced to each other.

            Migrations([common_migrations, billing_migrations])
                ->  migrations.contexts == {"common": common_migrations, "billing": ...}
            Migrations([billing_migrations, billing_migrations])
                ->  ValueError("contexts registered twice: billing")

        Two contexts sharing one folder are refused too; every engine is told which others
        migrate beside it (`MigrationEngine.coexist`).
        """
        self.system = MigrationSystem(contexts)
        self.moves = Moves(self.system)
        self.authoring = Authoring(self.system)

    @property
    def contexts(self) -> Mapping[str, ContextMigrations]:
        """The registered contexts by name."""
        return self.system.contexts

    def chain(self, context: str, store: str) -> Chain:
        """The chain of one store of one context, refused when either is not registered."""
        return self.system.chain(context, store)

    def status(self) -> MigrationStatus:
        """Where the whole system stands, reading every store once; nothing is refused but a
        timeline that cannot be built (a cycle, an unknown `requires`).

            migrations.status().timeline
            ->  [(Step("…a1", "baseline"), True), (Step("…b7", "add due_date"), False)]
        """
        return self.system.status()

    def upgrade_plan(self, to: str | None = None) -> list[Step]:
        """What `upgrade(to)` would apply, in order, running nothing — refused as it would be:
        when a body changed since it was hashed, or a store is ahead or dirty.

            migrations.upgrade_plan()            ->  [Step("…b7", "add due_date"),
                                                      Step("…c2", "add read_at")]
            migrations.upgrade_plan(to="…b7")    ->  [Step("…b7", "add due_date")]

        `to` is a step id or its start; the steps after it in the timeline are left out.
        """
        return self.moves.upgrade_plan(to)

    def upgrade(self, to: str | None = None) -> list[Step]:
        """Apply every pending step in timeline order — up to `to` when given — and answer what
        ran. Refused, before anything runs, as `upgrade_plan` says."""
        return self.moves.upgrade(to)

    def downgrade_plan(self, to: str) -> list[Step]:
        """What `downgrade(to)` would revert, newest first, running nothing — refused as it
        would be: when one of them is irreversible, or the system never stood on `to`.

            timeline: …a1 baseline (applied), …b7 add due_date (applied), …c2 add read_at
            migrations.downgrade_plan("…a1")   ->  [Step("…b7", "add due_date")]
            migrations.downgrade_plan("base")  ->  [Step("…b7", ...), Step("…a1", ...)]
        """
        return self.moves.downgrade_plan(to)

    def downgrade(self, to: str) -> list[Step]:
        """Put the whole system back to the step `to` — `"base"` for nothing applied — reverting
        every later applied step, newest first, across every context and store. Refused, before
        anything runs, as `downgrade_plan` says."""
        return self.moves.downgrade(to)

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
        """
        return self.authoring.revision(context, store, message, requires, irreversible)

    def resolve(self, context: str, store: str, at: str | None) -> None:
        """After a human looked at a dirty store: record the step it really stands on — `None`
        for none.

            migrations.resolve("billing", "main", "0192b7")   ->  None; clean on …b7
            migrations.resolve("billing", "main", None)       ->  None; clean, on no step

        `at` is a step id of this chain or its start; one that is neither is refused, and
        nothing is recorded.
        """
        self.moves.resolve(context, store, at)

    def adopt(self, context: str, store: str) -> Step:
        """Record a store that already holds what the code declares — a database another tool
        migrated — on its chain's last step, and answer that step. Refused, recording nothing,
        when the store already stands on a step, when its engine cannot tell drift — `resolve
        --at` is the manual form — or when it reports any.

            migrations.adopt("billing", "main")   ->  Step("0192…b7", message="add due_date")
            on a store missing a column:
                ->  MigrationRefused("billing/main does not hold what its steps lead to:
                    column invoice.due_date is declared but no step adds it")
        """
        return self.moves.adopt(context, store)

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
        """
        return self.authoring.hash()

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
        """
        return self.system.check()
