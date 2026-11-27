"""Data filters — global query filters: a rule a store applies to every read and checks every
write against, whoever reads or writes and through whichever door.

    class OnlyPublished(DataFilter):
        name = "published"

        def criteria(self, aggregate):
            if "state" not in fields_of(aggregate):
                return None
            return Criteria(selection=Condition(field="state", value="published"))

    database = Database(url, filters=[OnlyPublished()])
    repository.search(Articles)                       ->  … AND state = 'published'
    with DataFilters.disabled("published"):
        repository.search(Articles)                   ->  every state

The vocabulary is the industry's: EF Core calls them *global query filters*, ABP *data filters*,
Hibernate `@Filter`; SQLAlchemy executes them with `with_loader_criteria`. A filter knows nothing
of tenants, owners or states — it answers which aggregates it applies to and the `Criteria` it
adds, read when the statement runs. Tenancy, row-level access and any rule of a project are
filters of their own, registered on the store they protect.

Context: a filter is registered on the **store** (`Database(filters=...)`,
`MemoryRepository(filters=...)`), not on a repository instance, so a Feature called inside
another one's unit of work, an `EntityReads` and a plain session read through it alike. Turning
one off is per execution (`DataFilters.disabled`): one request never widens another's.
"""

from collections.abc import Generator, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
from typing import Any

from sincpro_framework.context import use_context
from sincpro_framework.ddd import exceptions
from sincpro_framework.ddd.criteria import (
    Criteria,
    Evaluation,
    Expression,
    Node,
    Resolution,
)
from sincpro_framework.ddd.entity.entity_meta import ambient_resolution


class DataFilter:
    """One rule every read of a store applies and every write is checked against.

        class ActiveOnly(DataFilter):
            name = "active"

            def criteria(self, aggregate):
                return Criteria(selection=Condition(field="active", value=True)) if … else None

    Only `criteria` is required. `stamp` fills what a new record leaves empty before it is
    checked (a tenant, an owner); `guards_writes = False` makes the filter a read-only view —
    a write outside it is allowed.
    """

    name: str = ""
    """What `DataFilters.disabled(name)` turns off; the class name when empty."""

    guards_writes: bool = True
    """Whether a record saved or removed outside the filter is refused — `False` for a default
    view (what is shown), `True` for an isolation (what may be touched)."""

    @property
    def key(self) -> str:
        """The name it is turned off by."""
        return self.name or type(self).__name__

    def criteria(self, aggregate: type) -> Criteria | None:
        """What every read of `aggregate` adds — `None` when the filter does not apply to it,
        or has nothing to add for this execution. Read when the statement runs, so a value of
        the context (`FromContext`) or read here from `use_context()` is the request's own."""
        raise NotImplementedError(f"{type(self).__name__}.criteria")

    def stamp(self, record: Any) -> None:
        """Fills what a new record leaves empty, before the write is checked. Nothing by default."""
        return None

    def refusal(self, record: Any) -> Exception:
        """What a write of `record` outside the filter raises — a `ProgrammingError` by default:
        the code wrote where its reader could not see. An isolation the caller reaches answers
        what fits the caller instead (tenancy: not found)."""
        return exceptions.ProgrammingError(
            f"{type(record).__name__} lies outside the data filter {self.key}; it can neither "
            "be written nor removed here"
        )


@dataclass(frozen=True)
class DataFilterState:
    """The filters an execution applies beyond its stores' own, and the ones it turned off —
    held in the execution context, so the nearest scope decides, nested calls inherit it, and
    a thread or a task handed the context carries it.

        use_context().get(DataFilterState)   ->  DataFilterState(applied=(TenantFilter(),),
                                                                 disabled=frozenset())
    """

    applied: tuple[DataFilter, ...] = ()
    disabled: frozenset[str] = frozenset()

    @staticmethod
    def current() -> "DataFilterState":
        """The state of the execution in play — nothing applied, nothing off, outside any."""
        found = use_context().get(DataFilterState)
        return found if isinstance(found, DataFilterState) else DataFilterState()

    def applying(self, filters: Iterable[DataFilter]) -> "DataFilterState":
        """This state with `filters` applied — each replacing one of its name, and on again if
        it was off."""
        added = list(filters)
        names = {one.key for one in added}
        return DataFilterState(
            (*(one for one in self.applied if one.key not in names), *added),
            self.disabled - names,
        )

    def disabling(self, names: Iterable[str]) -> "DataFilterState":
        """This state with the named filters off — applied or the stores' own alike."""
        off = frozenset(names)
        return DataFilterState(
            tuple(one for one in self.applied if one.key not in off), self.disabled | off
        )


def _resolved(expression: Expression | None) -> Expression | None:
    """The expression with its dynamic values read now, from the context in play."""
    if expression is not None and Resolution.has_dynamic_values(expression):
        return Resolution.resolve(expression, ambient_resolution())
    return expression


def holds_for(record: Any, expression: Expression | None) -> bool:
    """Whether `record` answers `expression`, its dynamic values read from the context in play —
    what a write is checked with, so a scope written with `FromContext` judges the record by
    the request's value instead of refusing every one."""
    resolved = _resolved(expression)
    return resolved is None or Evaluation.holds(resolved, partial(getattr, record))


def _criteria_expression(data_filter: DataFilter, aggregate: type) -> Expression | None:
    """What one filter adds to `aggregate`, as an expression."""
    added = data_filter.criteria(aggregate)
    return None if added is None else added.expression


class DataFilters:
    """The filters of one store, in the order they were added.

    filters = DataFilters(TenantFilter(...), OnlyPublished())
    filters.expression_for(Invoice)      ->  tenant_id IN (…) AND state = 'published'
    filters.admits(invoice)              ->  whether a read may hand it back
    filters.check_write(invoice, True)   ->  stamped, then refused when outside
    """

    def __init__(self, *filters: DataFilter) -> None:
        self._filters: list[DataFilter] = []
        for one in filters:
            self.add(one)

    def add(self, data_filter: DataFilter) -> "DataFilters":
        """Adds a filter; one with the same name is replaced — a filter is known by its name."""
        self._filters = [one for one in self._filters if one.key != data_filter.key]
        self._filters.append(data_filter)
        return self

    def remove(self, name: str) -> "DataFilters":
        """Takes the filter of that name away for good — `disabled` is the per-execution one."""
        self._filters = [one for one in self._filters if one.key != name]
        return self

    def __iter__(self) -> Iterator[DataFilter]:
        return iter(self._filters)

    def __len__(self) -> int:
        return len(self._filters)

    def __bool__(self) -> bool:
        return bool(self._filters)

    @staticmethod
    @contextmanager
    def applied(*filters: DataFilter) -> Generator[None]:
        """Applies the filters to every store the block reads or writes, in this execution only
        — the block and everything it calls, a nested Feature, a hook, a thread handed the
        context. No store needs to know them.

            with DataFilters.applied(TenantFilter(...)):
                repository.search(Invoices)          the tenant's

        The nearest block decides: one applied inside a block that turned it off is on again.
        """
        state = DataFilterState.current().applying(filters)
        with use_context().scoped({DataFilterState: state}):
            yield

    @staticmethod
    @contextmanager
    def disabled(*names: str) -> Generator[None]:
        """Turns the named filters off for the block, in this execution only — applied ones and
        a store's own alike: what reading across tenants, or the archived, asks for by name.

            with DataFilters.disabled("tenant"):
                repository.search(Invoices)          every tenant's
        """
        state = DataFilterState.current().disabling(names)
        with use_context().scoped({DataFilterState: state}):
            yield

    @staticmethod
    def is_disabled(name: str) -> bool:
        """Whether the execution in play turned the filter of that name off."""
        return name in DataFilterState.current().disabled

    def active(self) -> list[DataFilter]:
        """The filters this execution applies to this store: its own, unless turned off, and
        the ones the execution applied — one of the same name in place of the store's."""
        state = DataFilterState.current()
        applied = {one.key for one in state.applied}
        own = [
            one
            for one in self._filters
            if one.key not in state.disabled and one.key not in applied
        ]
        return [*own, *state.applied]

    def expression_for(
        self,
        aggregate: type,
        guarding: bool = False,
        active: list[DataFilter] | None = None,
    ) -> Expression | None:
        """What every active filter adds to a read of `aggregate`, joined with AND — `None`
        when none applies. `guarding` keeps only the filters that guard writes."""
        expression: Expression | None = None
        for one in self.active() if active is None else active:
            if guarding and not one.guards_writes:
                continue
            added = one.criteria(aggregate)
            if added is not None and added.expression is not None:
                expression = Node.combined(expression, added.expression)
        return expression

    def criteria_for(self, aggregate: type) -> Criteria | None:
        """`expression_for` as a `Criteria` — what a fingerprint or a narrowing takes."""
        expression = self.expression_for(aggregate)
        return None if expression is None else Criteria(selection=expression)

    def admits(self, record: Any) -> bool:
        """Whether a read may hand `record` back — what a read by identity, which may answer from
        memory without a statement, asks."""
        return holds_for(record, self.expression_for(type(record)))

    def stamp(self, record: Any) -> None:
        """Each active filter fills what `record` leaves empty — what a repository asks before
        the hooks of a save run, so they read the record as it will be stored."""
        for one in self.active():
            one.stamp(record)

    def check_write(self, record: Any, is_new: bool) -> None:
        """Stamps a new record, then refuses it when a filter that guards writes leaves it out.

        1. A new record: each active filter fills what it leaves empty.
        Final: the refusal of the first filter that guards writes and leaves the record out —
        a write is never made where its reader could not see it.
        """
        active = self.active()
        if is_new:
            self.stamp(record)
        for one in active:
            if one.guards_writes and not holds_for(
                record, _criteria_expression(one, type(record))
            ):
                raise one.refusal(record)

    def check_writes(self, records: Iterable[Any], is_new: bool) -> None:
        """`check_write` for each record."""
        for one in records:
            self.check_write(one, is_new)
