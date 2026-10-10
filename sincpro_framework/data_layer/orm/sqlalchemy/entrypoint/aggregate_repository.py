"""`DatabaseAggregateRepository[T]`: the baseline view of one aggregate, and every
capability the database repository has, with the aggregate already given.

    class Invoices(DatabaseAggregateRepository[Invoice]):
        def overdue(self, today: date) -> EntityCollection[Invoice]:
            return self.search(Criteria(where=Condition(field="due", operator="<", value=today)))

        def unpaid_by_customer(self) -> list[dict[str, Any]]:
            return self.group_by(["customer_id"], Criteria(where=…))

        def aged(self, today: date) -> EntityCollection[Invoice]:
            asked = Criteria(order_by=["due"])
            statement = self.statement(asked).where(InvoiceRow.due < today - timedelta(days=90))
            return self.run(statement, asked)            the door underneath, same envelope

    invoices = Invoices(self.repository)
    with invoices.context(isolation=Isolation.SERIALIZABLE) as unit:
        invoice = unit.get(invoice_id, for_update=True)  the same class, bound to one transaction
        invoice.pay()
        unit.save(invoice)

**Bound to the repository, never beside it.** Every call goes through `self.repository` — the
database repository a bounded context already injects — so hooks, scope, version checks and
named errors are the ones every other Feature gets. `context()` and `narrowed()` hand back this
same class over the bound repository, so a named question asked inside a transaction runs in it.
"""

from collections.abc import Callable, Generator, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any, Self

from sqlalchemy import Select
from sqlalchemy.orm import Session

from sincpro_framework.data_layer.orm.sqlalchemy.domain.transaction import Isolation, Writes
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.repository import Repository
from sincpro_framework.data_layer.orm.sqlalchemy.services.workflows.reading import Explained
from sincpro_framework.ddd.criteria import Bucket, Criteria, Level, Pivot, Specification
from sincpro_framework.ddd.entity.entity_collection import EntityCollection
from sincpro_framework.ddd.exceptions import StaleAggregate, TransactionConflict
from sincpro_framework.ddd.repositories.aggregate_repository import AggregateRepository
from sincpro_framework.ddd.repositories.capabilities import Upserted


class DatabaseAggregateRepository[T](AggregateRepository[T]):
    """One aggregate, read and written through the database repository with everything it
    answers: analysis, bulk writes, the unit of work, and SQLAlchemy underneath."""

    repository: Repository

    def __init__(self, repository: Repository, aggregate: type[T] | None = None) -> None:
        super().__init__(repository, aggregate)

    # --- the unit of work -------------------------------------------------------------------
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
        """This view over one transaction — `Repository.context`, with the same options."""
        with self.repository.context(
            isolation, read_only, timeout, engine, writes, separate
        ) as unit:
            yield self.bound_to(unit)

    def narrowed(self, scope: Criteria) -> Self:
        """This view through a filter nothing can widen — `Repository.narrowed`."""
        return self.bound_to(self.repository.narrowed(scope))

    @contextmanager
    def savepoint(self) -> Generator[None]:
        with self.repository.savepoint():
            yield

    def commit(self) -> None:
        self.repository.commit()

    def flush(self) -> None:
        self.repository.flush()

    def after_commit(self, callback: Callable[[], Any]) -> None:
        self.repository.after_commit(callback)

    def after_rollback(self, callback: Callable[[], Any]) -> None:
        self.repository.after_rollback(callback)

    def retrying[R](
        self,
        work: Callable[[], R],
        attempts: int = 3,
        on: type[Exception] | tuple[type[Exception], ...] = (
            StaleAggregate,
            TransactionConflict,
        ),
        wait: float = 0.05,
    ) -> R:
        return self.repository.retrying(work, attempts, on, wait)

    @property
    def session(self) -> Session:
        """The session of the unit of work in play — SQLAlchemy whole, inside the same
        transaction."""
        return self.repository.session

    # --- analysis ---------------------------------------------------------------------------
    def distinct(self, field: str, criteria: Criteria | None = None) -> list[Any]:
        return self.repository.distinct(self.aggregate, field, criteria)

    def measures(self, criteria: Criteria | None = None, **measures: Any) -> dict[str, Any]:
        return self.repository.measures(self.aggregate, criteria, **measures)

    def group_by(
        self, by: Sequence[str], criteria: Criteria | None = None
    ) -> list[dict[str, Any]]:
        return self.repository.group_by(self.aggregate, by, criteria)

    def group_by_levels(self, criteria: Criteria | None = None) -> list[Bucket]:
        return self.repository.group_by_levels(self.aggregate, criteria)

    def pivot(
        self,
        rows: Sequence[str | Level],
        columns: Sequence[str | Level],
        criteria: Criteria | None = None,
        **measures: Any,
    ) -> Pivot:
        return self.repository.pivot(self.aggregate, rows, columns, criteria, **measures)

    def export(
        self, criteria: Criteria | None = None, specification: Specification | None = None
    ) -> Iterator[dict[str, Any]]:
        return self.repository.export(self.aggregate, criteria, specification)

    # --- writing past the aggregate ---------------------------------------------------------
    def upsert(
        self,
        record: T | Sequence[T],
        on: Sequence[str],
        update: Sequence[str] | None = None,
    ) -> Upserted:
        return self.repository.upsert(record, on, update)

    def update_all(self, criteria: Criteria, values: Mapping[str, Any]) -> int:
        return self.repository.update_all(self.aggregate, criteria, values)

    def remove_all(self, criteria: Criteria) -> int:
        return self.repository.remove_all(self.aggregate, criteria)

    # --- the door underneath ----------------------------------------------------------------
    def statement(self, criteria: Criteria | None = None) -> Select:
        """This criteria as an ordinary `Select` over the aggregate's table, to take further."""
        return self.repository.statement(self.aggregate, criteria or Criteria())

    def run(self, statement: Select, criteria: Criteria | None = None) -> EntityCollection[T]:
        """A statement built from `statement()`, executed and wrapped in the usual envelope."""
        return self.repository.run(self.aggregate, statement, criteria or Criteria())

    def explain(self, criteria: Criteria | None = None) -> Explained:
        return self.repository.explain(self.aggregate, criteria)
