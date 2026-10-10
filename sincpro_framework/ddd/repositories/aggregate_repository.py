"""`AggregateRepository[T]`: one aggregate's repository, written on top of the repository a
bounded context already has — for when the domain wants its questions named.

    class Invoices(AggregateRepository[Invoice]):
        def overdue(self, today: date) -> EntityCollection[Invoice]:
            return self.search(Criteria(where=Condition(field="due", operator="<", value=today)))

    invoices = Invoices(self.repository)
    invoice = invoices.get(invoice_id)            no type to pass: the aggregate is the class's
    invoices.save(invoice)
    invoices.overdue(today)                       the domain's question, by its name

**Two flavours of one repository, never two repositories.** The generic one —
`repository.get(Invoice, id)` with a `Criteria` — answers everything and is what most Features
need. This one is a thin view over it, for an aggregate whose questions deserve names: it holds
no state, opens no session, and every call goes through the repository underneath, hooks,
scope and version check included. Nothing a project writes here can reach around the store,
and nothing the store does is skipped by coming through here.

**Optional, and never the only door.** A per-aggregate class for every aggregate is the cost
that made the pattern slow to write; here it is written only where a name pays for itself, and
`self.repository` stays the whole store for anything this view does not repeat.

The aggregate is read off the class's argument — `AggregateRepository[Invoice]` — or handed in,
`AggregateRepository(repository, Invoice)`, for a view nobody needs to name.
"""

from collections.abc import Iterator, Sequence
from copy import copy
from typing import Any, ClassVar, Self, cast, get_args, get_origin

from sincpro_framework.ddd.criteria import Criteria
from sincpro_framework.ddd.entity.entity_collection import Count, EntityCollection
from sincpro_framework.ddd.repositories.repository import IRepository
from sincpro_framework.exceptions import ProgrammingError


class AggregateRepository[T]:
    """The baseline of one aggregate — every read and write `Repository` answers, with the
    aggregate already given."""

    declared: ClassVar[type | None] = None
    """The aggregate a subclass named in its base, `AggregateRepository[Invoice]`."""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        for base in getattr(cls, "__orig_bases__", ()):
            origin, arguments = get_origin(base), get_args(base)
            if (
                isinstance(origin, type)
                and issubclass(origin, AggregateRepository)
                and arguments
                and isinstance(arguments[0], type)
            ):
                cls.declared = arguments[0]

    def __init__(self, repository: IRepository, aggregate: type[T] | None = None) -> None:
        """`aggregate` only when the class does not name one, or names a base of it — the class
        a table was mapped to, of the aggregate the view was written for."""
        named = aggregate if aggregate is not None else self.declared
        if named is None:
            raise ProgrammingError(
                f"{type(self).__name__} does not say which aggregate it holds: name it in the "
                "base, AggregateRepository[Invoice], or hand it in"
            )
        if self.declared is not None and not issubclass(named, self.declared):
            raise ProgrammingError(
                f"{type(self).__name__} holds {self.declared.__name__}, not {named.__name__}"
            )
        self.repository = repository
        self.aggregate = cast(type[T], named)

    def bound_to(self, repository: IRepository) -> Self:
        """The same view over another repository — the one a unit of work or a narrowing
        handed back. A copy, so a subclass keeps whatever else it was built with."""
        bound = copy(self)
        bound.repository = repository
        return bound

    # --- reading ----------------------------------------------------------------------------
    def get(
        self,
        identity: Any,
        for_update: bool = False,
        skip_locked: bool = False,
        nowait: bool = False,
        detail: Criteria | None = None,
    ) -> T | None:
        """The one record, with what `detail` asks to bring along. Not a page.

        account = accounts.get(account_id, detail=detail_of(Account))
        """
        return self.repository.get(
            self.aggregate, identity, for_update, skip_locked, nowait, detail
        )

    def search(
        self,
        criteria: Criteria | None = None,
        for_update: bool = False,
        skip_locked: bool = False,
        nowait: bool = False,
    ) -> EntityCollection[T]:
        return self.repository.search(
            self.aggregate, criteria, for_update, skip_locked, nowait
        )

    def count(self, criteria: Criteria | None = None) -> Count:
        return self.repository.count(self.aggregate, criteria)

    def browse(self, ids: Sequence[Any]) -> EntityCollection[T]:
        return self.repository.browse(self.aggregate, ids)

    def fetch_all(self, criteria: Criteria | None = None) -> EntityCollection[T]:
        return self.repository.fetch_all(self.aggregate, criteria)

    def stream(self, criteria: Criteria | None = None) -> Iterator[EntityCollection[T]]:
        return self.repository.stream(self.aggregate, criteria)

    def first(self, criteria: Criteria | None = None) -> T | None:
        return self.repository.first(self.aggregate, criteria)

    def one(self, criteria: Criteria | None = None) -> T:
        return self.repository.one(self.aggregate, criteria)

    def get_by(self, **values: Any) -> T | None:
        return self.repository.get_by(self.aggregate, **values)

    def exists(self, criteria: Criteria | None = None) -> bool:
        return self.repository.exists(self.aggregate, criteria)

    def pluck(self, field: str, criteria: Criteria | None = None) -> list[Any]:
        return self.repository.pluck(self.aggregate, field, criteria)

    # --- writing ----------------------------------------------------------------------------
    def save(self, record: T | Sequence[T] | EntityCollection[T]) -> None:
        self.repository.save(record)

    def remove(self, record: T | Sequence[T] | EntityCollection[T]) -> None:
        self.repository.remove(record)

    def archive(self, record: T | Sequence[T] | EntityCollection[T]) -> None:
        self.repository.archive(record)

    def record_changes(self, record: T) -> Any:
        """Writes down what changed about this aggregate now — `Repository.record_changes`."""
        return self.repository.record_changes(record)
