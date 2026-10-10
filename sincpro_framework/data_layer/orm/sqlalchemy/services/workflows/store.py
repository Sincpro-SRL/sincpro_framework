"""What reading and writing share: one database, the session a call runs in, the scope it was
narrowed to and what its transaction was opened with."""

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from sqlalchemy.orm import Session

from sincpro_framework.data_layer.orm.sqlalchemy.domain.transaction import Transaction
from sincpro_framework.data_layer.orm.sqlalchemy.infrastructure.database import Database
from sincpro_framework.data_layer.orm.sqlalchemy.infrastructure.unit_in_play import in_play
from sincpro_framework.data_layer.orm.sqlalchemy.services.aggregation import (
    WITHOUT_PERCENTILES,
)
from sincpro_framework.ddd.criteria import (
    Condition,
    Criteria,
    Operator,
    combined,
    conditions_of,
)
from sincpro_framework.ddd.criteria.evaluate import matches
from sincpro_framework.ddd.entity import ArchivableMixin
from sincpro_framework.ddd.entity.entity_meta import Meta
from sincpro_framework.ddd.exceptions import (
    ContractViolation,
)
from sincpro_framework.ddd.repositories.capabilities import StoreCapabilities
from sincpro_framework.ddd.repositories.hooks import Hooks
from sincpro_framework.ddd.repositories.repository import IRepository

WITHOUT_ROW_LOCKS = frozenset({"sqlite"})
"""The dialects that drop `FOR UPDATE`: SQLite locks the whole database on its first write, so
there is no row to hold — the second writer fails at its write instead."""

WITH_SKIP_LOCKED = frozenset({"postgresql", "mysql", "mariadb", "oracle"})
"""The dialects that render `SKIP LOCKED` and `NOWAIT`."""


class Store(IRepository):
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
    ) -> None:
        """`hooks` second, so a wiring reads as what it is — `Repository(database, billing_hooks)`.
        The rest are what a unit of work and a narrowing carry: its session, its scope, what
        the transaction was opened with, and the repository they are a view of."""
        super().__init__(hooks, guard)
        self.database = database
        self._held = session
        self._scope = scope
        self._opened_with = transaction or Transaction()

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

    def _dialect(self) -> str:
        """The engine underneath, by name. Read for the one decision that depends on it:
        whether it can compute a percentile."""
        return self.database.engine.dialect.name

    def _asked(self, model: type, meta: Meta, criteria: Criteria) -> Any:
        """The filter that actually runs: what the caller asked, and what the repository adds.

        1. The scope, when this repository is narrowed, refused outright if this aggregate
           cannot answer it.
        2. Final: the archived left out, unless the caller named `archived_at` itself.
        """
        expression = criteria.expression
        if self._scope is not None:
            kept, dropped = meta.accept(self._scope.expression)
            if dropped or kept is None:
                raise ContractViolation(
                    f"{meta.aggregate} cannot answer the scope this repository was narrowed "
                    f"by ({', '.join(one.field for one in dropped) or 'nothing survived'}); "
                    "reading it wide is not an option"
                )
            expression = combined(kept, expression)
        if issubclass(model, ArchivableMixin) and not any(
            one.field == "archived_at" for one in conditions_of(expression)
        ):
            expression = combined(
                expression,
                Condition(field="archived_at", operator=Operator.IS_NULL, value=True),
            )
        return expression

    def _in_scope(self, record: Any) -> bool:
        """Whether a record belongs to what this repository may see and write."""
        return self._scope is None or matches(record, self._scope.expression)

    def _refuse_outside(self, record: Any) -> None:
        if not self._in_scope(record):
            raise ContractViolation(
                f"{type(record).__name__} lies outside what this repository was narrowed to; "
                "it can neither be written nor removed here"
            )

    def _refuse_read_only(self) -> None:
        if self._transaction.read_only:
            raise ContractViolation(
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
