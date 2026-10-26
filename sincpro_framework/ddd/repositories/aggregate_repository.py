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

from sincpro_framework.ddd import exceptions
from sincpro_framework.ddd.criteria import Criteria
from sincpro_framework.ddd.entity.entity_collection import Count, EntityCollection
from sincpro_framework.ddd.repositories.repository import IRepository


class AggregateRepository[T]:
    """The baseline of one aggregate — every read and write `Repository` answers, with the
    aggregate already given."""

    declared: ClassVar[type | None] = None
    """The aggregate a subclass named in its base, `AggregateRepository[Invoice]`."""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Reads the aggregate off the base a subclass writes, so `get` needs no type.

            class Invoices(AggregateRepository[Invoice]): ...
            Invoices.declared  ->  Invoice

        1. Each generic base the class wrote (`__orig_bases__`), in order.
        2. Final: one whose origin is an `AggregateRepository` and whose first argument is a
           class names the aggregate; a subclass of a subclass with no argument inherits its
           parent's.
        """
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
        a table was mapped to, of the aggregate the view was written for.

            Invoices(repository)                     ->  .aggregate is Invoice
            # class Invoices(AggregateRepository[Invoice])
            Invoices(repository, MappedInvoice)      ->  .aggregate is MappedInvoice
            AggregateRepository(repository, Invoice) ->  .aggregate is Invoice
            AggregateRepository(repository)
                ->  raises ProgrammingError("… does not say which aggregate it holds …")
            Invoices(repository, Partner)
                ->  raises ProgrammingError("Invoices holds Invoice, not Partner")

        The class the view holds is checked here, once, so every read and write after it
        can trust it.
        """
        named = aggregate if aggregate is not None else self.declared
        if named is None:
            raise exceptions.ProgrammingError(
                f"{type(self).__name__} does not say which aggregate it holds: name it in the "
                "base, AggregateRepository[Invoice], or hand it in"
            )
        if self.declared is not None and not issubclass(named, self.declared):
            raise exceptions.ProgrammingError(
                f"{type(self).__name__} holds {self.declared.__name__}, not {named.__name__}"
            )
        self.repository = repository
        self.aggregate = cast(type[T], named)

    def bound_to(self, repository: IRepository) -> Self:
        """The same view over another repository — the one a unit of work or a narrowing
        handed back. A copy, so a subclass retains whatever else it was built with."""
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
        """One page of this aggregate the criteria matches — `ReadsAggregates.search`."""
        return self.repository.search(
            self.aggregate, criteria, for_update, skip_locked, nowait
        )

    def count(self, criteria: Criteria | None = None) -> Count:
        """How many of this aggregate the criteria matches — `ReadsAggregates.count`."""
        return self.repository.count(self.aggregate, criteria)

    def browse(self, ids: Sequence[Any]) -> EntityCollection[T]:
        """The records with these ids, the missing ones simply absent."""
        return self.repository.browse(self.aggregate, ids)

    def fetch_all(self, criteria: Criteria | None = None) -> EntityCollection[T]:
        """Every match in one collection, unpaged — for a result known to be small."""
        return self.repository.fetch_all(self.aggregate, criteria)

    def stream(self, criteria: Criteria | None = None) -> Iterator[EntityCollection[T]]:
        """Every match, a piece at a time — for a result too large to hold at once."""
        return self.repository.stream(self.aggregate, criteria)

    def first(
        self,
        criteria: Criteria | None = None,
        for_update: bool = False,
        skip_locked: bool = False,
        nowait: bool = False,
    ) -> T | None:
        """The first match, or `None` — claimed as `get` claims it."""
        return self.repository.first(
            self.aggregate, criteria, for_update, skip_locked, nowait
        )

    def one(
        self,
        criteria: Criteria | None = None,
        for_update: bool = False,
        skip_locked: bool = False,
        nowait: bool = False,
    ) -> T:
        """The single record the criteria matches; none is `AggregateNotFound`."""
        return self.repository.one(self.aggregate, criteria, for_update, skip_locked, nowait)

    def get_by(
        self,
        for_update: bool = False,
        skip_locked: bool = False,
        nowait: bool = False,
        **values: Any,
    ) -> T | None:
        """One record by a natural key, `None` when absent.

        invoices.get_by(number="F-0042")  ->  Invoice(...) | None
        """
        return self.repository.get_by(
            self.aggregate, for_update, skip_locked, nowait, **values
        )

    def exists(self, criteria: Criteria | None = None) -> bool:
        """Whether anything of this aggregate matches, without reading it."""
        return self.repository.exists(self.aggregate, criteria)

    def pluck(self, field: str, criteria: Criteria | None = None) -> list[Any]:
        """One field of every match, without building the aggregates."""
        return self.repository.pluck(self.aggregate, field, criteria)

    # --- writing ----------------------------------------------------------------------------
    def save(self, record: T | Sequence[T] | EntityCollection[T]) -> None:
        """Writes one record or several through the repository underneath, hooks included."""
        self.repository.save(record)

    def remove(self, record: T | Sequence[T] | EntityCollection[T]) -> None:
        """Deletes one record or several — deletes, never archives."""
        self.repository.remove(record)

    def archive(self, record: T | Sequence[T] | EntityCollection[T]) -> None:
        """Puts one record or several away without deleting them (`ArchivableMixin` only)."""
        self.repository.archive(record)

    def record_changes(self, record: T) -> Any:
        """Writes down what changed about this aggregate now — `Repository.record_changes`."""
        return self.repository.record_changes(record)
