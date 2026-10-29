"""What reading and writing share: one database, the session a call runs in, the scope it was
narrowed to and what its transaction was opened with."""

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from sqlalchemy.orm import Session

from sincpro_framework.data_layer import exceptions
from sincpro_framework.data_layer.orm.sqlalchemy.domain.transaction import Transaction
from sincpro_framework.data_layer.orm.sqlalchemy.infrastructure.database import Database
from sincpro_framework.data_layer.orm.sqlalchemy.infrastructure.unit_in_play import in_play
from sincpro_framework.data_layer.orm.sqlalchemy.services.aggregation import (
    WITHOUT_PERCENTILES,
)
from sincpro_framework.data_layer.orm.sqlalchemy.services.event_mapping import is_mapped_event
from sincpro_framework.ddd.criteria import (
    Condition,
    Criteria,
    Evaluation,
    Node,
    Operator,
)
from sincpro_framework.ddd.entity import ArchivableMixin
from sincpro_framework.ddd.entity.entity_meta import Meta
from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.ddd.repositories.capabilities import StoreCapabilities
from sincpro_framework.ddd.repositories.hooks import Hooks
from sincpro_framework.ddd.repositories.publication import Publishes, PublishOnCommit
from sincpro_framework.ddd.repositories.repository import IRepository

WITHOUT_ROW_LOCKS = frozenset({"sqlite"})
"""The dialects that drop `FOR UPDATE`: SQLite locks the whole database on its first write, so
there is no row to hold — the second writer fails at its write instead."""

WITH_SKIP_LOCKED = frozenset({"postgresql", "mysql", "mariadb", "oracle"})
"""The dialects that render `SKIP LOCKED` and `NOWAIT`."""


class RepositoryState(IRepository):
    """One database seen through one session, one scope and one transaction — the state every
    reading and every write of the repository runs in."""

    def __init__(
        self,
        database: Database,
        hooks: Hooks | None = None,
        session: Session | None = None,
        scope: Criteria | None = None,
        guard: "IRepository | None" = None,
        transaction: Transaction | None = None,
        publish_on_commit: PublishOnCommit | Publishes | None = None,
    ) -> None:
        """`hooks` second, so a wiring reads as what it is — `Repository(database, billing_hooks)`.
        The rest are what a unit of work and a narrowing carry: its session, its scope, what
        the transaction was opened with, and the repository they are a view of.

            Repository(database, billing_hooks)
                ->  no session held (it joins a unit of work in play), no scope,
                    Transaction() — what the database does on its own
            Repository(database, session=session, scope=mine, transaction=asked)
                ->  the view `context()` hands its block, bound to that session

        `publish_on_commit` hands what a write recorded to a publisher once it committed — a
        unit of work and a narrowing publish as the repository they are a view of.

            Repository(database, billing_hooks, publish_on_commit=rabbit)

        Nothing is opened here: a repository holds a session only when it is handed one.
        """
        super().__init__(hooks, guard, publish_on_commit)
        self.database = database
        self._held = session
        self._scope = scope
        self._opened_with = transaction or Transaction()

    def _keeps(self, kind: type) -> bool:
        """An event is held only when its class has an event table; every other kind is."""
        return not issubclass(kind, DomainEvent) or is_mapped_event(kind)

    @property
    def _bound(self) -> Session | None:
        """The session this repository runs in: the one it was handed by `context()`, or the
        unit of work in play on its database — so a Feature called inside another one's
        `context()` writes in that transaction instead of committing on its own."""
        if self._held is not None:
            return self._held
        joined = in_play(self.database)
        return joined.session if joined is not None else None

    @property
    def _transaction(self) -> Transaction:
        """What the transaction it runs in was opened with — read-only, writes saved — the
        joined one's when it joined."""
        if self._held is None:
            joined = in_play(self.database)
            if joined is not None:
                return joined.transaction
        return self._opened_with

    def _dialect(self) -> str:
        """The engine underneath, by name. Read for the one decision that depends on it:
        whether it can compute a percentile."""
        return self.database.engine.dialect.name

    @property
    def capabilities(self) -> StoreCapabilities:
        """Read off the engine underneath, because one class runs on several.

        Postgres    row locks · skip_locked · nowait · savepoints · percentiles
        SQLite      savepoints — no row locks, no percentiles
        """
        dialect = self._dialect()
        return StoreCapabilities(
            row_locks=dialect not in WITHOUT_ROW_LOCKS,
            skip_locked=dialect in WITH_SKIP_LOCKED,
            nowait=dialect in WITH_SKIP_LOCKED,
            savepoints=True,
            percentiles=dialect not in WITHOUT_PERCENTILES,
        )

    def _asked(self, model: type, meta: Meta, criteria: Criteria) -> Any:
        """The filter that actually runs: what the caller asked, and what the repository adds.

        1. The scope, when this repository is narrowed, refused outright if this aggregate
           cannot answer it.
        2. Final: the archived left out, unless the caller named `archived_at` itself.
        """
        expression = criteria.expression
        if self._scope is not None:
            accepted, dropped = meta.accept(self._scope.expression)
            if dropped or accepted is None:
                raise exceptions.ProgrammingError(
                    f"{meta.aggregate} cannot answer the scope this repository was narrowed "
                    f"by ({', '.join(one.field for one in dropped) or 'nothing survived'}); "
                    "reading it wide is not an option"
                )
            expression = Node.combined(accepted, expression)
        if issubclass(model, ArchivableMixin) and not any(
            one.field == "archived_at"
            for one in (expression.conditions() if expression is not None else [])
        ):
            expression = Node.combined(
                expression,
                Condition(field="archived_at", operator=Operator.IS_NULL, value=True),
            )
        return expression

    def _in_scope(self, record: Any) -> bool:
        """Whether a record belongs to what this repository may see and write."""
        return self._scope is None or Evaluation.matches(record, self._scope.expression)

    def _refuse_outside(self, record: Any) -> None:
        """A record outside the scope this repository was narrowed to is refused — a narrowed
        repository writes and removes only what it can read."""
        if not self._in_scope(record):
            raise exceptions.ProgrammingError(
                f"{type(record).__name__} lies outside what this repository was narrowed to; "
                "it can neither be written nor removed here"
            )

    def _refuse_read_only(self) -> None:
        """Any write inside a unit of work opened `read_only=True` is refused, before the
        engine is asked."""
        if self._transaction.read_only:
            raise exceptions.ProgrammingError(
                "this unit of work was opened read_only=True; nothing may be written in it"
            )

    @contextmanager
    def _session(self) -> Generator[Session]:
        """The session a call runs in: the bound one inside a unit of work, a fresh one
        otherwise. The fresh one commits when the call ends; the bound one commits when the
        unit of work does."""
        if self._bound is not None:
            yield self._bound
            return
        with self.database.session() as session:
            yield session
