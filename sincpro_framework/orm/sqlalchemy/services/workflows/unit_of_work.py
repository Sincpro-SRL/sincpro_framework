"""The unit of work: one transaction for several reads and writes, configured where it begins,
with its savepoints, its batched commits, what waits for its commit, and the retry of one that
lost a race — and the narrowing a tenant or a permission is."""

from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from dataclasses import replace
from time import sleep
from typing import Any, Self

from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.orm import Session

from sincpro_framework.ddd.criteria import (
    Criteria,
)
from sincpro_framework.ddd.exceptions import (
    ContractViolation,
    StaleAggregate,
    TransactionConflict,
)
from sincpro_framework.orm.sqlalchemy.domain.relations import REPOSITORY
from sincpro_framework.orm.sqlalchemy.domain.transaction import Isolation, Transaction, Writes
from sincpro_framework.orm.sqlalchemy.infrastructure.transaction_hooks import (
    Callback,
    closed_savepoint,
    opened_savepoint,
    register,
)
from sincpro_framework.orm.sqlalchemy.infrastructure.transaction_opening import began
from sincpro_framework.orm.sqlalchemy.infrastructure.unit_in_play import playing
from sincpro_framework.orm.sqlalchemy.services.workflows.store import Store
from sincpro_framework.sincpro_logger import logger


class UnitOfWork(Store):
    """Context: a bound repository is the same class over one session, built with `type(self)`, so
    the unit of work never needs to know which facade it is part of."""

    def narrowed(self, scope: Criteria) -> Self:
        """The same database seen through a filter nothing can widen: what a tenant, a branch
        or a permission is.

            books = repository.narrowed(Criteria(where=Condition(field="tenant", value="acme")))
            books.search(Invoices, criteria)      →  … AND tenant = 'acme'
            books.save(invoice_of_another_tenant) →  ContractViolation

        Every reading merges the scope with AND, and every write is checked against it before
        it reaches the database, with the same evaluator the translator is specified by. An
        aggregate the scope cannot be expressed on is refused outright rather than read wide:
        a scope that is silently dropped is the one bug this exists to prevent.

        Narrowing again narrows further; the scopes accumulate, they never replace.
        """
        return type(self)(
            self.database,
            session=self._held,
            scope=self._scope.merged_with(scope) if self._scope is not None else scope,
            guard=self._guard,
            transaction=self._opened_with,
        )

    @property
    def session(self) -> Session:
        """The session a unit of work runs in — the escape hatch for what the criteria will
        never say: a join across four tables, a window function, a bulk `UPDATE`, raw SQL.

            with self.repository.context() as repository:
                rows = repository.session.execute(select(Line.account_id, func.sum(Line.amount))…)

        Everything SQLAlchemy has, inside the same transaction, with the same stamping of
        `updated_at` and the same tracing. Only inside a unit of work: outside there is no
        session to hand over, and a caller that wanted one for a single statement wants
        `statement()` and `run()`.
        """
        if self._bound is None:
            raise ContractViolation(
                "the session exists only inside context(); open one, or use "
                "statement() and run() for a single read"
            )
        return self._bound

    def flush(self) -> None:
        """Writes what is pending to the database without committing — so a following read
        in the same unit of work sees it, and a constraint fails here rather than at the end.
        """
        self.session.flush()

    def _discarded_unsaved(self, session: Session) -> None:
        """Context: every `save` flushes on the spot, so what is still changed here was changed
        after its last save, or never saved — and the commit would write it past the aggregate's
        hooks and cascade. It is put back as the database holds it, and named."""
        unsaved = [one for one in session.dirty if session.is_modified(one)]
        if not unsaved:
            return
        for one in unsaved:
            try:
                session.refresh(one)
            except InvalidRequestError:
                session.expunge(one)  # its row is gone: there is nothing to put back
        named = ", ".join(sorted({type(one).__name__ for one in unsaved}))
        logger.warning(
            f"{named} changed inside context() and was never saved; the change was discarded "
            "— save() writes an aggregate (context(writes=Writes.CHANGED) writes what changed)"
        )

    def commit(self) -> None:
        """Ends the current transaction and starts the next one, in the same unit of work.

            with self.repository.context() as repository:
                for batch in repository.stream(Line, criteria):
                    reconcile(batch)
                    repository.commit()                    each batch is durable on its own

        For a process that runs long: one transaction held across a thousand groups is a
        lock held for the whole run, and a failure at the end loses everything. The objects
        in hand stay usable — nothing expires on commit. What changed and was never saved is
        put back first, as at the end of the block.
        """
        if self._transaction.writes is Writes.SAVED:
            self._discarded_unsaved(self.session)
        self.session.commit()

    def _registered(self, moment: str, callback: Callback) -> None:
        if self._bound is None:
            raise ContractViolation(
                f"{moment} belongs to a unit of work; outside context() every call commits on "
                "its own, so what would wait for the commit can simply run now"
            )
        register(self._bound, moment, callback)

    def after_commit(self, callback: Callback) -> None:
        """Runs `callback` once this unit of work has committed; never if it is undone.

            with self.repository.context() as unit:
                unit.save(invoice)
                unit.after_commit(lambda: publisher.publish_all(invoice.pull_events()))

        **The moment to tell the world**: inside the block a fact can still be taken back, here
        it cannot. Registered inside a `savepoint()` that rolls back, it is dropped with it.
        A callback that raises is logged and the others still run — the commit stands, so the
        caller is not told otherwise. It cannot write through this unit of work any more; what
        it writes goes through a new one.
        """
        self._registered("after_commit", callback)

    def after_rollback(self, callback: Callback) -> None:
        """Runs `callback` once this unit of work — or the savepoint it was registered in — was
        undone: what was promised on the way can be let go of."""
        self._registered("after_rollback", callback)

    @contextmanager
    def savepoint(self) -> Generator[None]:
        """A part of the unit of work that can fail on its own.

            with self.repository.context() as repository:
                for group in groups:
                    try:
                        with repository.savepoint():
                            reconcile(group)          raises → only this group is undone
                    except ReconciliationError:
                        report(group)

        What a savepoint is for: the groups that reconciled stay reconciled, the one that
        did not is rolled back to here, and the unit of work goes on.
        """
        session = self.session
        opened_savepoint(session)
        try:
            with session.begin_nested():
                yield
        except BaseException:
            closed_savepoint(session, undone=True)
            raise
        closed_savepoint(session, undone=False)

    @contextmanager
    def context(
        self,
        isolation: Isolation | None = None,
        read_only: bool = False,
        timeout: float | None = None,
        engine: Mapping[str, Any] | None = None,
        writes: Writes | None = None,
        separate: bool = False,
    ) -> Generator[Self]:
        """Several reads and writes as one transaction.

            with self.repository.context() as repository:
                run = repository.get(Run, run_id)          the same session
                run.advance()
                repository.save(run)                       flushed here, committed when the block ends
            an exception inside                     →  everything rolled back

        **The transaction is configured where it begins.**

            context(isolation=Isolation.SERIALIZABLE)       a balance two requests must not both pass
            context(read_only=True)                 a report: any write is refused before it runs
            context(timeout=5.0)                    seconds any one statement may run (Postgres)
            context(engine={"postgresql_deferrable": True})   SQLAlchemy's own, passed as is

        An isolation level the database lacks is refused, because a Feature that passed on one
        engine would lose its race on the other — except where a stronger one honours it:
        SQLite runs every level as serializable. A timeout it cannot honour is warned about.

        **What is written is what was saved.** A change to an aggregate that never reached `save`
        is put back when the block ends and named in the log: the commit never writes past the
        aggregate's hooks and cascade. `writes=Writes.CHANGED` makes the commit write what the
        block changed on what it loaded, as the session tracks it — saved or not, hooks or not.
        `flush()` and `session` stay the doors for writing what SQLAlchemy holds on purpose.

        The engine handed to the block is this same engine bound to one session, so it
        answers every method the outer one does, and what it was narrowed to still holds
        inside. Nesting reuses the session in play; a nested block that asks for different
        options is refused, because the transaction they would configure has already begun.

        **Every repository on this database joins it while the block runs** — the one a Feature
        called through the bus holds included (`infrastructure/unit_in_play.py`):

            with self.repository.context():
                self.feature_bus(CommandPostInvoices(…), ResponsePostInvoices)
                self.feature_bus(CommandReconcile(…), ResponseReconcile)
            →  both commit together, or neither does

        `separate=True` opens a transaction of its own even inside another — what a record that
        must outlive the outer rollback needs, an audit of the attempt. It needs a database with
        a connection pool: an in-memory SQLite has one connection, and two transactions cannot
        share it.
        """
        asked = Transaction(
            isolation, read_only, timeout, dict(engine or {}), writes or Writes.SAVED
        )
        if self._bound is not None and not separate:
            given = (
                (isolation, timeout, writes) != (None, None, None)
                or read_only
                or bool(engine)
            )
            joined = replace(asked, writes=writes or self._transaction.writes)
            if given and joined != self._transaction:
                raise ContractViolation(
                    "this unit of work already began with other options; a nested context() "
                    "joins the transaction in play and cannot configure it again"
                )
            yield self
            return
        with self.database.session() as session:
            if asked.writes is Writes.SAVED:
                # A read never writes what a block changed: a write is a save().
                session.autoflush = False
            began(session, asked)
            bound = type(self)(
                self.database,
                session=session,
                scope=self._scope,
                guard=self._guard,
                transaction=asked,
            )
            # A relation touched inside the block resolves itself through this repository.
            session.info[REPOSITORY] = bound
            with playing(self.database, session, asked):
                yield bound
                if asked.read_only:
                    # Nothing the block changed in memory reaches the commit; what it read
                    # stays usable after it, and the commit still runs what waited for it.
                    session.expunge_all()
                elif asked.writes is Writes.SAVED:
                    self._discarded_unsaved(session)

    def retrying[T](
        self,
        work: Callable[[], T],
        attempts: int = 3,
        on: type[Exception] | tuple[type[Exception], ...] = (
            StaleAggregate,
            TransactionConflict,
        ),
        wait: float = 0.05,
    ) -> T:
        """Runs a unit of work again when it lost a race, and gives up saying so — a newer
        version of what it read, or another transaction it conflicted with.

            def post() -> ResponsePostEntry:
                with self.repository.context() as ledger:
                    …
            answer = self.repository.retrying(post)

        A callable and not a block, because a `with` cannot run its body twice. The wait
        doubles between attempts so two workers that collided do not collide again on the
        same beat; the last failure is raised as it was, not wrapped.
        """
        if attempts < 1:
            raise ContractViolation("retrying needs at least one attempt")
        if self._bound is not None:
            raise ContractViolation(
                "retrying runs a whole unit of work again; inside context() the transaction "
                "that lost is the one in play — call it on the repository, around the context()"
            )
        for attempt in range(attempts):
            try:
                return work()
            except on:
                if attempt == attempts - 1:
                    raise
                sleep(wait * (2**attempt))
        raise AssertionError("unreachable")
