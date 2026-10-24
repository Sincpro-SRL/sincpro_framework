"""A repository with no database behind it: the same vocabulary, answered over records held in
memory.

    accounts = MemoryRepository(Account(code="1010", …), Account(code="2010", …))
    bus.add_dependency("repository", accounts)

What it is for is testing a Feature without a database and without a fake: the filter is
answered by `matches`, the same evaluator the SQL translator is checked against, so a double
that passes here agrees with the engine by construction. What it is not for is production; it
holds everything it was given and offers no transaction.

Four things it deliberately does the way the adapter does, because a Feature can see them: the
version is raised on every save and a stale one is refused, `updated_at` is stamped, who wrote it
is stamped when an `actor` was given, and an archived aggregate is left out of a reading that did
not ask for it. **What it hands back is the record it holds, not a copy.** Two callers in one process end up
with the same object, so one's changes are visible to the other before any save — and a
`StaleAggregate` race, which needs two holders of two versions, cannot happen between them. It
is what the store is: a dict. Against a database each session builds its own instance, and the
race is real.

To exercise that race against this double, hold copies:

    first = copy.deepcopy(repository.get(Account, account_id))
    second = copy.deepcopy(repository.get(Account, account_id))
    repository.save(first)        # version moves
    repository.save(second)       # StaleAggregate, the same as the engine

It has the database's unit of work (`memory_unit.py`): `context()` commits or puts everything
back, a change never saved is put back, `for_update` holds a row until the block ends, and
`after_commit` waits for it — so a Feature that writes in a unit of work is tested here as it runs
there. Outside `context()` every call stands on its own, as it does on the engine.

Two things it does not have: relations and date grains, which is where a database starts — and
`remove` here deletes, the way it does there; `archive` is the other one.
"""

import dataclasses
from collections.abc import Callable, Generator, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from copy import copy
from decimal import Decimal
from typing import Any, Self, overload

from sincpro_framework.data_layer import exceptions
from sincpro_framework.data_layer.repositories.memory_unit import (
    MemoryUnit,
    RowLocks,
    ran,
    restored,
)
from sincpro_framework.ddd.criteria import (
    Bucket,
    Condition,
    CountMode,
    Criteria,
    CursorKeys,
    Grouping,
    Level,
    Measure,
    Operator,
    Sort,
    axis_values,
    check_levels,
    check_measures,
    combined,
    conditions_of,
    empty_measure,
    has_dynamic_values,
    matches,
    parse_order,
    part_of,
    resolve,
)
from sincpro_framework.ddd.entity import ArchivableMixin, Entity, extensions, utc_now
from sincpro_framework.ddd.entity.computed import computations_of
from sincpro_framework.ddd.entity.entity_collection import (
    Count,
    Dropped,
    EntityCollection,
    identity_name,
    identity_of,
    model_and_collection,
)
from sincpro_framework.ddd.entity.entity_meta import (
    FieldMeta,
    Meta,
    ambient_resolution,
    describe_class,
)
from sincpro_framework.ddd.entity.mixins.event_sourced import EventSourcedMixin
from sincpro_framework.ddd.events.domain_event import NAME, DomainEvent
from sincpro_framework.ddd.events.mixins.deliverable import (
    DELIVERY_FIELDS,
    DeliverableEventMixin,
)
from sincpro_framework.ddd.repositories.capabilities import (
    Analyzes,
    Extends,
    StoreCapabilities,
    Transacts,
    Upserted,
    WritesInBulk,
)
from sincpro_framework.ddd.repositories.hooks import Hooks
from sincpro_framework.ddd.repositories.mixins.change_tracking import (
    ChangeTrackingRepositoryMixin,
)
from sincpro_framework.ddd.repositories.reads import note_read
from sincpro_framework.ddd.repositories.repository import (
    IRepository,
    records_of,
    refuse_locking,
    refuse_unarchivable,
    refuse_wiring_as_a_record,
    refuse_writing_in_preview,
)
from sincpro_framework.sincpro_logger import logger


def _percentile(values: list[Any], fraction: float) -> Any:
    """The value at a fraction of the sorted values, interpolating between the two it falls
    between — the same `percentile_cont` a database computes, so a Feature tested here and run
    against Postgres reads the same number.

    >>> _percentile([1, 2, 3, 4], 0.5)
    2.5
    """
    if not values:
        return None
    ordered = sorted(values)
    at = fraction * (len(ordered) - 1)
    below, above = int(at), min(int(at) + 1, len(ordered) - 1)
    if below == above:
        return ordered[below]
    # A decimal column is interpolated as a decimal, because a decimal times a float is an error.
    reach = Decimal(str(at - below)) if isinstance(ordered[below], Decimal) else at - below
    return ordered[below] + (ordered[above] - ordered[below]) * reach


MEASURES: dict[str, Callable[..., Any]] = {
    "count": lambda values: len(values),
    "count_distinct": lambda values: len(set(values)),
    "sum": lambda values: sum(values),
    "min": lambda values: min(values, default=None),
    "max": lambda values: max(values, default=None),
    "avg": lambda values: (sum(values) / len(values)) if values else None,
    "percentile": _percentile,
    "median": lambda values: _percentile(values, 0.5),
    "bool_and": lambda values: all(values) if values else None,
    "bool_or": lambda values: any(values) if values else None,
}


def _measure_of(values: list[Any], measure: Measure) -> Any:
    """One measure over the values a group holds. The only place that knows a percentile takes
    its fraction and every other function takes nothing."""
    if measure.function == "percentile":
        return MEASURES["percentile"](values, measure.argument)
    return MEASURES[measure.function](values)


def _sorted(records: list[Any], sorts: tuple[Sort, ...]) -> list[Any]:
    """The records in the criteria's order, one sort at a time from the last to the first, so
    an earlier sort wins over a later one — what a stable sort gives for free."""
    ordered = list(records)
    for sort in reversed(sorts):
        ordered.sort(key=lambda record: getattr(record, sort.field), reverse=sort.descending)
    return ordered


def _after(records: list[Any], keys: CursorKeys, sorts: tuple[Sort, ...]) -> list[Any]:
    """The rows past the one a cursor names, compared the way the keyset compares them.

    The first sort that differs decides, in its own direction; a row equal on every key is the
    row the cursor was minted from and does not come back.
    """

    def beyond(record: Any) -> bool:
        """Whether `record` comes after the cursor — the first key that differs decides."""
        for value, sort in zip(keys.keys, sorts):
            mine = getattr(record, sort.field)
            if mine == value:
                continue
            return bool(mine < value) if sort.descending else bool(mine > value)
        return False

    return [record for record in records if beyond(record)]


def _by(values: dict[str, Any]) -> Criteria:
    """A criteria that asks for exactly these values, the natural-key lookup `get_by` is."""
    conditions = [
        Condition(field=field, operator=Operator.EQ, value=value)
        for field, value in values.items()
    ]
    return Criteria(where=combined(*conditions))


def _flattened_buckets(buckets: list[Bucket], path: tuple = ()) -> list[tuple[tuple, Bucket]]:
    """The leaves of a bucket tree, each with the path of group values that leads to it — what
    `group_by` turns into one flat row per combination.

        [Bucket(value="BO", groups=[Bucket(value="LPZ"), Bucket(value="SCZ")])]
        ->  [(("BO", "LPZ"), <LPZ bucket>), (("BO", "SCZ"), <SCZ bucket>)]
    """
    flat = []
    for bucket in buckets:
        here = path + (bucket.value,)
        flat.extend(
            _flattened_buckets(bucket.groups, here) if bucket.groups else [(here, bucket)]
        )
    return flat


def _measured(rows: list[Any], grouping: Grouping) -> dict[str, Any]:
    """The grouping's measures over the rows of one group, a `None` left out the way SQL's
    aggregates skip a NULL.

        rows with amount 10, None, 30; measures={"total": sum of amount}  ->  {"total": 40}
    """
    return {
        name: _measure_of(
            [
                getattr(record, measure.field)
                for record in rows
                if getattr(record, measure.field) is not None
            ],
            measure,
        )
        for name, measure in grouping.measures.items()
    }


def _page_of_ids(
    rows: list[Any], criteria: Criteria, meta: Meta
) -> tuple[list[Any], str | None]:
    """The ids of the first page of a group, and the cursor to the next — what a bucket
    carries so a screen opens a group without a second query.

    Context: only filled when the criteria asked for a page; otherwise a bucket answers counts
    and measures alone, as the engine does.

    1. No page asked: no ids, no cursor.
    2. The criteria's order (or the aggregate's default), the identity appended as the last
       tie-breaker so the cursor names exactly one row.
    3. The rows sorted and cut to `criteria.limit`.
    Final: the ids kept, and a cursor from the last one when more rows remain.
    """
    if not criteria.pagination.asked:
        return [], None
    sorts = criteria.order or parse_order(meta.default_order)
    if not any(sort.field == meta.identity for sort in sorts):
        sorts = sorts + (Sort(field=meta.identity, descending=sorts[0].descending),)
    ordered = _sorted(rows, sorts)
    kept = ordered[: criteria.limit]
    cursor = (
        criteria.pagination.next_from(kept[-1], sorts)
        if kept and len(ordered) > criteria.limit
        else None
    )
    return [getattr(record, meta.identity) for record in kept], cursor


def _bucket_key(bucket: Bucket, name: str) -> Any:
    """What a group is sorted by for one key of the grouping's order: `count`, one of its
    measures, or else its own value as text with nothing last.
    """
    if name == "count":
        return bucket.count
    if name in bucket.measures:
        return bucket.measures[name]
    return (bucket.value is None, str(bucket.value))


def _by_value(bucket: Bucket) -> tuple[bool, Any]:
    """A group's own value as the database orders it: numbers as numbers, text as text, and
    nothing last."""
    return (bucket.value is None, 0 if bucket.value is None else bucket.value)


def _ordered_buckets(buckets: list[Bucket], grouping: Grouping) -> list[Bucket]:
    """The groups of one level in the grouping's order, cut to its page — the `ORDER BY` and
    `LIMIT/OFFSET` the database applies to a `GROUP BY`.

    1. No order asked: by the group's own value — numbers as numbers, text as text, nothing
       last; values that cannot be compared with each other fall back to their text.
    2. An order asked: one stable sort per key, from the last to the first, so the first key
       wins — by `count`, a measure, or the value.
    3. No page on the grouping: every group.
    Final: the groups of the page asked for.
    """
    if not grouping.order:
        try:
            buckets.sort(key=_by_value)
        except TypeError:
            buckets.sort(key=lambda bucket: (bucket.value is None, str(bucket.value)))
    else:
        for sort in reversed(grouping.order):
            buckets.sort(
                key=lambda bucket, name=sort.field: _bucket_key(bucket, name),
                reverse=sort.descending,
            )
    if grouping.pagination is None:
        return buckets
    start = grouping.pagination.skipped()
    return buckets[start : start + grouping.pagination.limit]


def _value_of(record: Any, level: Level) -> Any:
    """The group a record lands in for a level: its field, or the part of a date it holds."""
    value = getattr(record, level.field)
    return value if level.part is None else part_of(value, level.part)


def _tree(
    rows: list[Any],
    grouping: Grouping,
    criteria: Criteria,
    levels: tuple[Level, ...],
    meta: Meta,
    where: Any,
) -> list[Bucket]:
    """One level of groups and the levels below it, with the same having, order and page the
    database would apply. A level that fills lists the values with no rows as groups of 0."""
    if not levels:
        return []
    level, rest = levels[0], levels[1:]
    by_value: dict[Any, list[Any]] = {}
    for record in rows:
        by_value.setdefault(_value_of(record, level), []).append(record)
    if level.fill:
        field = meta.field(level.field)
        for value in axis_values(level, field.choices, field.type.value == "boolean", where):
            by_value.setdefault(value, [])

    having = grouping.where_measures
    if has_dynamic_values(having):
        having = resolve(having, ambient_resolution())

    buckets = []
    for value, mine in by_value.items():
        opens = (
            criteria
            if level.part is not None
            else criteria.merged_with(
                Criteria(
                    where=Condition(field=level.field, operator=Operator.EQ, value=value)
                )
            ).model_copy(update={"grouping": Grouping()})
        )
        measures = (
            _measured(mine, grouping)
            if mine
            else {name: empty_measure(one) for name, one in grouping.measures.items()}
        )
        if having is not None and not matches(_Measured(count=len(mine), **measures), having):
            continue
        ids, cursor = _page_of_ids(mine, criteria, meta)
        buckets.append(
            Bucket(
                field=level.field,
                value=value,
                count=len(mine),
                measures=measures,
                criteria=opens,
                ids=ids,
                cursor=cursor,
                groups=_tree(mine, grouping, opens, rest, meta, where),
                openable=level.opens,
            )
        )
    return _ordered_buckets(buckets, grouping)


class _Measured:
    """What `having` is evaluated against: the numbers a group folded, as attributes."""

    def __init__(self, **values: Any) -> None:
        """Each measure by name: `_Measured(total=120, count=3).total  ->  120`."""
        self.__dict__.update(values)


class _Lenient:
    """A record as a filter reads it: a field its class does not have — an extension's, on a record
    that is not one — is absent, as its column is NULL in the database. A field the aggregate never
    had was already dropped before the filter ran."""

    def __init__(self, record: Any) -> None:
        """`record` is read through, untouched."""
        self._record = record

    def __getattr__(self, name: str) -> Any:
        """The record's field, or `None` for one it does not have."""
        return getattr(self._record, name, None)


class MemoryRepository(
    ChangeTrackingRepositoryMixin, IRepository, Analyzes, WritesInBulk, Extends, Transacts
):
    """Every read and write the vocabulary can answer without a database, a unit of work
    included."""

    @property
    def capabilities(self) -> StoreCapabilities:
        """Percentiles, computed the way a database does; row locks held by a unit of work
        across the threads of the process; no savepoints."""
        return StoreCapabilities(
            percentiles=True, row_locks=True, skip_locked=True, nowait=True
        )

    def __init__(
        self,
        *records: Any,
        hooks: Hooks | None = None,
        actor: "Callable[[], str | None] | None" = None,
    ) -> None:
        """`actor` answers who is writing, for an `AuditedMixin` aggregate — the same argument
        `Database` takes and read the same way, once per save. Without one those fields stay
        `None`, which is also what the engine does.

            repository = MemoryRepository(Invoice(id="F-1"), Invoice(id="F-2"), hooks=hooks)
            repository.get(Invoice, "F-1")     ->  Invoice(id="F-1")
            MemoryRepository(hooks)            ->  ProgrammingError, write it as `hooks=`

        The records are seeded as they are, with no version check; each repository keeps its
        own unit of work per context and its own row locks.
        """
        refuse_wiring_as_a_record(records)
        self._actor = actor
        super().__init__(hooks)
        self._stored: dict[type, dict[Any, Any]] = {}
        self._units: ContextVar[MemoryUnit | None] = ContextVar(
            f"memory_unit_{id(self)}", default=None
        )
        self._row_locks = RowLocks()
        self.add(*records)

    def add(self, *records: Any) -> "MemoryRepository":
        """Seeds it, without the version check a save makes: how a test arranges its world."""
        for record in records:
            self._stored.setdefault(type(record), {})[identity_of(record)] = record
        return self

    def _touching(self, kind: type, identity: Any) -> None:
        """A write of the block in play reaches this record: kept as it was, and marked saved."""
        unit = self._units.get()
        if unit is not None:
            unit.remember((kind, identity), self._stored.get(kind, {}).get(identity))
            unit.saved.add((kind, identity))

    def _read(self, record: Any) -> Any:
        """A record handed to the block in play is kept as it was first, so what the block changes
        on it can be put back."""
        unit = self._units.get()
        if unit is not None and record is not None and not isinstance(record, DomainEvent):
            key = (type(record), identity_of(record))
            if self._stored.get(key[0], {}).get(key[1]) is record:
                unit.remember(key, record)
        return super()._read(record)

    def _claimed(
        self, records: list[Any], for_update: bool, skip_locked: bool, nowait: bool
    ) -> list[Any]:
        """The records the block in play now holds, the ones another holds passed over with
        `skip_locked`. Outside a unit of work a claim is refused: nothing would hold it."""
        if not (for_update or skip_locked or nowait):
            return records
        unit = self._units.get()
        if unit is None:
            refuse_locking(True)
            return records
        return [
            one
            for one in records
            if self._row_locks.claim(unit, (type(one), identity_of(one)), skip_locked, nowait)
        ]

    @contextmanager
    def context(self) -> Generator[Self]:
        """One unit of work: committed when the block ends — a change never saved put back —
        every record it touched put back when it raises. A block inside another joins it.

            with repository.context() as unit:
                invoice = unit.get(Invoice, "F-1"); invoice.total = 900; unit.save(invoice)
            repository.get(Invoice, "F-1").total   ->  900
            with repository.context() as unit:
                unit.get(Invoice, "F-1").total = 1                     never saved
            repository.get(Invoice, "F-1").total   ->  900, put back and named in the log

        1. A unit already in play in this context: the block joins it, nothing more.
        2. A new unit, set as the one in play.
        3. The block raises: every record it touched put back as it was, its row locks
           released, the unit let go of, its `after_rollback` callbacks run — and the error
           raised again.
        4. The block ends: each record read but never saved put back, and logged.
        Final: its row locks released, the unit let go of, its `after_commit` callbacks run.
        """
        if self._units.get() is not None:
            yield self
            return
        unit = MemoryUnit()
        token = self._units.set(unit)
        try:
            yield self
        except BaseException:
            for key, before in unit.before.items():
                restored(self._stored, key, before)
            self._row_locks.release(unit)
            self._units.reset(token)
            ran(unit.after_rollback, "after_rollback")
            raise
        unsaved = [key for key in unit.before if key not in unit.saved]
        for key in unsaved:
            restored(self._stored, key, unit.before[key])
        if unsaved:
            logger.info(
                "changed in a unit of work and never saved, put back: "
                + ", ".join(f"{kind.__name__} {identity}" for kind, identity in unsaved)
            )
        self._row_locks.release(unit)
        self._units.reset(token)
        ran(unit.after_commit, "after_commit")

    def _registered(self, moment: str) -> list[Callable[[], Any]]:
        """The callbacks the unit of work in play runs at `moment` — `after_commit` or
        `after_rollback`.

        Context: outside `context()` there is no commit to wait for, so registering one is a
        mistake in the caller, refused as a `ProgrammingError` rather than run at once
        silently.
        """
        unit = self._units.get()
        if unit is None:
            raise exceptions.ProgrammingError(
                f"{moment} belongs to a unit of work; outside context() every call commits on "
                "its own, so what would wait for the commit can simply run now"
            )
        return unit.after_commit if moment == "after_commit" else unit.after_rollback

    def after_commit(self, callback: Callable[[], Any]) -> None:
        """Runs `callback` once the unit of work in play committed; never if it is undone."""
        self._registered("after_commit").append(callback)

    def after_rollback(self, callback: Callable[[], Any]) -> None:
        """Runs `callback` once the unit of work in play was undone."""
        self._registered("after_rollback").append(callback)

    def _extensions_held(self, aggregate: type) -> list[type]:
        """The subclasses of `aggregate` this store holds records of, the shallowest first."""
        return sorted(
            (
                kind
                for kind, held in self._stored.items()
                if held and kind is not aggregate and issubclass(kind, aggregate)
            ),
            key=lambda kind: len(kind.__mro__),
        )

    def definition(self, aggregate: type) -> Meta:
        """What may be asked about this aggregate, read off its annotations — and, once the store
        holds a record of a subclass of it, the fields that subclass adds, each marked with it as
        its owner and absent on a record that is not one, as the database does for an extension.

            repository.definition(Move).fields.keys()   ->  {"id", "number", "total", …}
            repository.add(MoveDelivery(id=7, route="R1"))
            repository.definition(Move).fields["route"].owner   ->  "MoveDelivery"

        An added field is described as nullable whatever its type says; a field two held
        subclasses add is described by the shallowest one.
        """
        meta = describe_class(aggregate, identity_name(aggregate))
        inherited = set(meta.fields)
        added: dict[str, FieldMeta] = {}
        for kind in self._extensions_held(aggregate):
            for name, field in describe_class(kind, identity_name(kind)).fields.items():
                if name not in inherited and name not in added:
                    added[name] = FieldMeta.for_column(
                        field.type,
                        True,
                        field.choices,
                        field.exact,
                        choice_labels=field.choice_labels,
                    ).model_copy(update={"owner": kind.__name__})
        return meta.model_copy(update={"fields": {**meta.fields, **added}}) if added else meta

    def _held_as(self, aggregate: type) -> list[dict[Any, Any]]:
        """Everything stored as `aggregate` or as an extension of it: a record is read as the class
        it was saved as, whoever asks."""
        return [
            held
            for kind, held in self._stored.items()
            if issubclass(kind, aggregate) and held
        ] or [self._stored.setdefault(aggregate, {})]

    def _with_computed(self, record: Any) -> Any:
        """The record with its computed fields set from `python`, which is what a store in memory
        answers where a database would run the field's `sql`. A field with only `sql` keeps its
        default here, and `_refuse_unanswerable` stops a criteria from asking for it."""
        for name, computation in computations_of(type(record)).items():
            if computation.python is not None:
                setattr(record, name, computation.python(record))
        return record

    def _refuse_unanswerable(self, aggregate: type, names: Sequence[str]) -> None:
        """A computed field with no `python` cannot be answered in memory, and guessing its
        value would be a second implementation that quietly differs: it is refused, naming it.
        """
        for name in names:
            computation = computations_of(aggregate).get(name)
            if computation is not None and computation.python is None:
                raise exceptions.InvalidCriteria(
                    f"{aggregate.__name__}.{name} is computed in SQL and has no `python`: a "
                    "memory store cannot filter, order or group by it — give "
                    "`computed(python=…)` its value over a record"
                )

    def _filterable(self, aggregate: type) -> Meta:
        """What a filter may name: an event is read by its envelope — its name, its subject, its
        flow, its delivery — as a context's event table holds them in columns; what a subclass
        adds lives in the row's payload, so a filter on it is dropped as `unknown_field` here as
        it is in SQL."""
        meta = self.definition(aggregate)
        if not (isinstance(aggregate, type) and issubclass(aggregate, DomainEvent)):
            return meta
        envelope = {one.name for one in dataclasses.fields(DomainEvent)}
        columns = envelope | DELIVERY_FIELDS | {NAME}
        kept = {name: field for name, field in meta.fields.items() if name in columns}
        # The delivery columns are the table's, so a base event class that is not deliverable
        # itself is still filtered by them — what the relay asks of a context's base class.
        delivery = describe_class(DeliverableEventMixin).fields
        return meta.model_copy(update={"fields": {**delivery, **kept}})

    def _rows(self, aggregate: type) -> list[Any]:
        """The records of `aggregate` — of every subclass too when it is a domain event, the way
        a context's one event table answers its base class."""
        note_read(aggregate)
        if isinstance(aggregate, type) and issubclass(aggregate, DomainEvent):
            found = [
                record
                for kind, held in self._stored.items()
                if issubclass(kind, aggregate)
                for record in held.values()
            ]
            return sorted(found, key=identity_of)
        return [
            self._with_computed(record)
            for held in self._held_as(aggregate)
            for record in held.values()
        ]

    def _live(self, aggregate: type, criteria: Criteria) -> bool:
        """Whether the archived have to be left out: they do, unless the criteria named them."""
        if not (isinstance(aggregate, type) and issubclass(aggregate, ArchivableMixin)):
            return False
        return not any(
            one.field == "archived_at" for one in conditions_of(criteria.expression)
        )

    def _matching(
        self, aggregate: type, criteria: Criteria, handed_back: bool = True
    ) -> tuple[list[Any], list[Dropped]]:
        """The records this criteria selects.

        `handed_back` is whether the caller will actually receive them. A reading that answers a
        number or a column — `count`, `pluck`, `measures`, a grouping — materialises nothing on
        the engine, which computes it in SQL, so firing `after_read` here would run a hook N
        times in a test and zero times in production. A hook that decrypts, masks or replaces
        an aggregate must see exactly the ones somebody is handed.
        """
        meta = self.definition(aggregate)
        self._refuse_unanswerable(
            aggregate, [leaf.field for leaf in conditions_of(criteria.expression)]
        )
        expression, dropped = self._filterable(aggregate).accept(criteria.expression)
        _, unanswerable = meta.accept_specification(criteria.specification)
        rows = [
            self._read(record) if handed_back else record
            for record in self._rows(aggregate)
            if matches(_Lenient(record), expression)
        ]
        if self._live(aggregate, criteria):
            rows = [record for record in rows if not record.is_archived]
        return rows, dropped + unanswerable

    def _ordering(self, aggregate: type, criteria: Criteria) -> tuple[Sort, ...]:
        """The sorts a read applies: the criteria's, or the aggregate's default order, each
        checked as orderable, with the identity appended as the last tie-breaker unless
        already there — the same total order the engine gives, so a cursor names exactly one
        row.

            Criteria(order="-issued_at")  ->  (Sort("issued_at", desc), Sort("id", desc))
        """
        meta = self.definition(aggregate)
        sorts = criteria.order or parse_order(meta.default_order)
        self._refuse_unanswerable(aggregate, [sort.field for sort in sorts])
        for sort in sorts:
            meta.orderable(sort.field)
        if any(sort.field == meta.identity for sort in sorts):
            return sorts
        return sorts + (Sort(field=meta.identity, descending=sorts[0].descending),)

    def _events_about(self, aggregate: type, identity: Any) -> list[DomainEvent]:
        """Every domain event stored about one aggregate — its type's name and its id — which
        is what an event-sourced aggregate is rebuilt from.
        """
        return [
            event
            for event in self._rows(DomainEvent)
            if event.entity_type == aggregate.__name__ and event.entity_id == identity
        ]

    def get[T](
        self,
        target: type[T],
        identity: Any,
        for_update: bool = False,
        skip_locked: bool = False,
        nowait: bool = False,
        detail: Criteria | None = None,
    ) -> T | None:
        """One record by its identity, or `None`; an archived one answers `None` too.

        `detail` is checked against the definition exactly as `search` checks a specification.
        The record held here already carries its relations, so nothing more is resolved.

            repository.get(Invoice, "F-1")      ->  Invoice(id="F-1", total=500)
            repository.get(Invoice, "gone")     ->  None
            repository.get(Partner, archived)   ->  None
            repository.get(Move, 7)             ->  MoveDelivery(id=7, …), as it was saved

        1. `detail`, when given, checked against the aggregate's definition.
        2. An event-sourced aggregate is rebuilt from the events stored about it.
        3. The record under that identity, stored as the class or as an extension of it.
        4. `None` for an archived record, or one `skip_locked` passed over; the claim asked
           for is taken — or refused outside a unit — even when nothing was found.
        Final: the record with its computed fields, handed back through `after_read`.
        """
        aggregate, _holder = model_and_collection(target)
        if detail is not None:
            self.definition(aggregate).accept_specification(detail.specification)
        if isinstance(aggregate, type) and issubclass(aggregate, EventSourcedMixin):
            return self._read(
                aggregate.rebuilt(identity, self._events_about(aggregate, identity))
            )
        note_read(aggregate)
        found = next(
            (held[identity] for held in self._held_as(aggregate) if identity in held), None
        )
        if found is not None and isinstance(found, ArchivableMixin) and found.is_archived:
            return None
        if found is not None and not self._claimed([found], for_update, skip_locked, nowait):
            return None
        if found is None:
            self._claimed([], for_update, skip_locked, nowait)
        return self._read(found if found is None else self._with_computed(found))

    @overload
    def browse[C: EntityCollection](self, target: type[C], ids: Sequence[Any]) -> C: ...

    @overload
    def browse[T](self, target: type[T], ids: Sequence[Any]) -> EntityCollection[T]: ...

    def browse(self, target: type, ids: Sequence[Any]) -> Any:
        """The records with these ids, in the order the ids were given, the ones not held left
        out.

        What this one does differently: it reads the dict directly — no filter runs, so an
        archived record is handed back like any other — and `after_read` fires for each
        record.

            repository.browse(Invoice, ["F-5", "F-1", "gone"])
                ->  EntityCollection([F-5, F-1]), count 2 exact

        Each record is handed back with its computed fields.
        """
        aggregate, holder = model_and_collection(target)
        note_read(aggregate)
        stored = {
            key: record for held in self._held_as(aggregate) for key, record in held.items()
        }
        found = [self._read(self._with_computed(stored[key])) for key in ids if key in stored]
        return self._searched(
            target,
            holder(
                items=tuple(found),
                count=Count(value=len(found), exact=True),
                meta=self.definition(aggregate),
            ),
        )

    @overload
    def search[C: EntityCollection](
        self,
        target: type[C],
        criteria: Criteria | None = None,
        for_update: bool = False,
        skip_locked: bool = False,
        nowait: bool = False,
    ) -> C: ...

    @overload
    def search[T](
        self,
        target: type[T],
        criteria: Criteria | None = None,
        for_update: bool = False,
        skip_locked: bool = False,
        nowait: bool = False,
    ) -> EntityCollection[T]: ...

    def search(
        self,
        target: type,
        criteria: Criteria | None = None,
        for_update: bool = False,
        skip_locked: bool = False,
        nowait: bool = False,
    ) -> Any:
        """The page this criteria asks for, with its cursor, its count and what was dropped —
        claimed by the unit of work in play with `for_update`.

            seven invoices, Criteria(pagination=Pagination(limit=3))
            repository.search(Invoice, criteria)
                ->  EntityCollection(3 items, cursor="…", count=Count(7, exact=True))
            repository.search(Invoice, criteria.resuming_from(that.cursor))
                ->  the next 3, and the last page holds 1 with no cursor

        1. The records the criteria matches, and what it dropped — not handed back yet.
        2. The claim asked for, taken on every match — `skip_locked` leaves out the ones
           another unit holds.
        3. Ordered by the criteria's order, the identity breaking ties.
        4. Resumed past the cursor's keys when it carries one; then the offset skipped.
        5. The page: up to `limit` records, each handed back through `after_read`; more past
           it means a cursor from the last one.
        Final: the collection `target` names — the count exact unless none was asked, the
        definition only when `criteria.meta` asks.
        """
        criteria = criteria or Criteria()
        aggregate, holder = model_and_collection(target)
        # Matched without reading: the page is cut first, and only what the caller is handed
        # goes through `after_read`. Read here, a paged walk would run the hook once per row per
        # page — seven rows over three pages is twenty-one — while the engine, which fetches
        # only the page, runs it seven times.
        rows, dropped = self._matching(aggregate, criteria, handed_back=False)
        rows = self._claimed(rows, for_update, skip_locked, nowait)
        sorts = self._ordering(aggregate, criteria)
        ordered = _sorted(rows, sorts)

        keys = criteria.pagination.keys_for(sorts)
        walked = _after(ordered, keys, sorts) if keys is not None else ordered
        walked = walked[criteria.pagination.skipped() :]
        kept = [self._read(one) for one in walked[: criteria.limit]]
        has_more = len(walked) > criteria.limit

        return self._searched(
            target,
            holder(
                items=tuple(kept),
                cursor=(
                    criteria.pagination.next_from(kept[-1], sorts)
                    if has_more and kept
                    else None
                ),
                count=(
                    None
                    if criteria.count is CountMode.NONE
                    else Count(value=len(rows), exact=True)
                ),
                dropped=tuple(dropped),
                meta=self.definition(aggregate) if criteria.meta else None,
            ),
        )

    @overload
    def fetch_all[C: EntityCollection](
        self, target: type[C], criteria: Criteria | None = None
    ) -> C: ...

    @overload
    def fetch_all[T](
        self, target: type[T], criteria: Criteria | None = None
    ) -> EntityCollection[T]: ...

    def fetch_all(self, target: type, criteria: Criteria | None = None) -> Any:
        """Every record the criteria matches, as one complete collection.

            over_100 = Criteria(where={"field": "total", "operator": ">", "value": 100})
            repository.fetch_all(Invoice, over_100)
                ->  EntityCollection(4 items, no cursor, count=Count(4, exact=True))

        Ordered as `search` orders them; no page and no cursor, whatever the criteria asks.
        """
        criteria = criteria or Criteria()
        aggregate, holder = model_and_collection(target)
        rows, dropped = self._matching(aggregate, criteria)
        ordered = _sorted(rows, self._ordering(aggregate, criteria))
        return self._searched(
            target,
            holder(
                items=tuple(ordered),
                count=Count(value=len(ordered), exact=True),
                dropped=tuple(dropped),
                meta=self.definition(aggregate) if criteria.meta else None,
            ),
        )

    @overload
    def stream[C: EntityCollection](
        self, target: type[C], criteria: Criteria | None = None
    ) -> Iterator[C]: ...

    @overload
    def stream[T](
        self, target: type[T], criteria: Criteria | None = None
    ) -> Iterator[EntityCollection[T]]: ...

    def stream(self, target: type, criteria: Criteria | None = None) -> Iterator[Any]:
        """Every page of the criteria, one `search` after another, until a page has no cursor.

        What this one does differently: nothing is held open between pages — each page is a
        new `search` resumed from the last cursor, so a record written meanwhile may appear in
        a later page as it would with keyset pagination on the engine.

            seven invoices, Criteria(pagination=Pagination(limit=3))
            [len(page) for page in repository.stream(Invoice, criteria)]   ->  [3, 3, 1]

        A generator: each page is read when the caller asks for the next one.
        """
        criteria = criteria or Criteria()
        while True:
            page = self.search(target, criteria)
            yield page
            if page.cursor is None:
                return
            criteria = criteria.resuming_from(page.cursor)

    def count(self, target: type, criteria: Criteria | None = None) -> Count:
        """How many records the criteria matches — always exact here, since every record is
        already in memory. No `after_read` fires: nothing is handed back.
        """
        aggregate, _holder = model_and_collection(target)
        rows, _ = self._matching(aggregate, criteria or Criteria(), handed_back=False)
        return Count(value=len(rows), exact=True)

    def exists(self, target: type, criteria: Criteria | None = None) -> bool:
        """Whether the criteria matches anything — a `count` above zero, there being no
        cheaper way over a dict.
        """
        return self.count(target, criteria).value > 0

    @overload
    def first[T](
        self,
        target: type[EntityCollection[T]],
        criteria: Criteria | None = None,
        for_update: bool = False,
        skip_locked: bool = False,
        nowait: bool = False,
    ) -> T | None: ...

    @overload
    def first[T](
        self,
        target: type[T],
        criteria: Criteria | None = None,
        for_update: bool = False,
        skip_locked: bool = False,
        nowait: bool = False,
    ) -> T | None: ...

    def first(
        self,
        target: type,
        criteria: Criteria | None = None,
        for_update: bool = False,
        skip_locked: bool = False,
        nowait: bool = False,
    ) -> Any:
        """The first record of the criteria's order, or `None` — the first item of the
        `search` page, claimed with the same flags.
        """
        return self.search(target, criteria, for_update, skip_locked, nowait).first()

    @overload
    def one[T](
        self,
        target: type[EntityCollection[T]],
        criteria: Criteria | None = None,
        for_update: bool = False,
        skip_locked: bool = False,
        nowait: bool = False,
    ) -> T: ...

    @overload
    def one[T](
        self,
        target: type[T],
        criteria: Criteria | None = None,
        for_update: bool = False,
        skip_locked: bool = False,
        nowait: bool = False,
    ) -> T: ...

    def one(
        self,
        target: type,
        criteria: Criteria | None = None,
        for_update: bool = False,
        skip_locked: bool = False,
        nowait: bool = False,
    ) -> Any:
        """The single record the criteria matches: none is `AggregateNotFound`, several a
        `ProgrammingError`.

            code_1010 = Criteria(where={"field": "code", "operator": "=", "value": "1010"})
            repository.one(Account, code_1010)   ->  Account(code="1010", …)
            no match                             ->  AggregateNotFound("no Account matches …")
            two matches                          ->  ProgrammingError("expected exactly one
                                                     record, found 2")

        The claim asked for is taken on every match before the count is judged; a match
        `skip_locked` passed over does not count.
        """
        found = self.fetch_all(target, criteria)
        claimed = self._claimed(list(found.items), for_update, skip_locked, nowait)
        found = found.filtered(lambda one: any(one is kept for kept in claimed))
        if not found.items:
            raise exceptions.AggregateNotFound(
                f"no {model_and_collection(target)[0].__name__} matches what was asked"
            )
        return found.ensure_one()

    @overload
    def get_by[T](
        self,
        target: type[EntityCollection[T]],
        for_update: bool = False,
        skip_locked: bool = False,
        nowait: bool = False,
        **values: Any,
    ) -> T | None: ...

    @overload
    def get_by[T](
        self,
        target: type[T],
        for_update: bool = False,
        skip_locked: bool = False,
        nowait: bool = False,
        **values: Any,
    ) -> T | None: ...

    def get_by(
        self,
        target: type,
        for_update: bool = False,
        skip_locked: bool = False,
        nowait: bool = False,
        **values: Any,
    ) -> Any:
        """One record by a natural key: the values that identify it besides its id.

            repository.get_by(Account, code="1010")   ->  Account(code="1010", …)
            repository.get_by(Account, code="9999")   ->  None
            repository.get_by(Account, kind="asset")  ->  ProgrammingError("3 records answer
                                                          to kind='asset'; a natural key …")
            repository.get_by(Account)                ->  InvalidCriteria, no value given

        Every value must hold — an equality per field — and the claim asked for is taken on
        the matches before they are counted.
        """
        if not values:
            raise exceptions.InvalidCriteria(
                "get_by needs at least one value, e.g. code='1010'"
            )
        found = self.fetch_all(target, _by(values))
        claimed = self._claimed(list(found.items), for_update, skip_locked, nowait)
        found = found.filtered(lambda one: any(one is kept for kept in claimed))
        if len(found) > 1:
            named = ", ".join(f"{field}={value!r}" for field, value in values.items())
            raise exceptions.ProgrammingError(
                f"{len(found)} records answer to {named}; a natural key names one"
            )
        return found.first()

    def pluck(self, target: type, field: str, criteria: Criteria | None = None) -> list[Any]:
        """One field of every match, in the criteria's order. The field is checked against the
        definition first, so a typo is refused rather than answered with `None`s; no
        `after_read` fires, as the engine builds no aggregate either.

            repository.pluck(Account, "code")  ->  ["1010", "2010", "3010"]
        """
        aggregate, _holder = model_and_collection(target)
        self.definition(aggregate).field(field)
        rows, _ = self._matching(aggregate, criteria or Criteria(), handed_back=False)
        return [
            getattr(record, field)
            for record in _sorted(rows, self._ordering(aggregate, criteria or Criteria()))
        ]

    def distinct(
        self, target: type, field: str, criteria: Criteria | None = None
    ) -> list[Any]:
        """The values that field takes across the matches, each once, sorted by their text
        with `None` last.

            repository.distinct(Account, "kind")  ->  ["asset", "liability", None]
        """
        seen = []
        for value in self.pluck(target, field, criteria):
            if value not in seen:
                seen.append(value)
        return sorted(seen, key=lambda value: (value is None, str(value)))

    def measures(
        self, target: type, criteria: Criteria | None = None, **measures: Any
    ) -> dict[str, Any]:
        """Measures over the whole result set, each named by the caller.

        in      Dataset, None, rows=("sum", "row_count")
        out     {'rows': 4012933}

            repository.measures(Invoice, None, total=("sum", "total"), top=("max", "total"))
                ->  {"total": Decimal("1250.00"), "top": Decimal("900.00")}
            repository.measures(Invoice)   ->  InvalidCriteria, no measure asked

        1. Refused with no measure.
        2. The aggregate's definition, and the records the criteria matches — not handed
           back, so no `after_read` fires.
        3. Per measure: read as `Measure.read` writes it, its field checked on the definition,
           and its function folded over the field's values — `None`s left out, as SQL does.
        Final: the answers by the caller's names.
        """
        if not measures:
            raise exceptions.InvalidCriteria(
                "measures needs at least one measure, e.g. rows=('sum', 'row_count')"
            )
        aggregate, _holder = model_and_collection(target)
        meta = self.definition(aggregate)
        rows, _ = self._matching(aggregate, criteria or Criteria(), handed_back=False)
        answer = {}
        for name, written in measures.items():
            measure = Measure.read(written)
            meta.field(measure.field)
            values = [
                getattr(record, measure.field)
                for record in rows
                if getattr(record, measure.field) is not None
            ]
            answer[name] = _measure_of(values, measure)
        return answer

    def _grouped(self, target: type, criteria: Criteria, grouping: Grouping) -> list[Bucket]:
        """The levels the grouping names, split in memory. A **grain** is the adapter's: cutting a
        date to a month is SQL a database writes, and a double that guessed it would be a second
        implementation to keep in step. A **part** is extraction — a number a date holds — so it is
        answered here, by the same arithmetic the database does."""
        aggregate, holder = model_and_collection(target)
        meta = self.definition(aggregate)
        check_levels(criteria, grouping)
        check_measures(
            grouping, {name: field.type.value for name, field in meta.fields.items()}
        )
        for level in grouping.group_by:
            meta.field(level.field)
            if level.grain is not None:
                raise exceptions.ProgrammingError(
                    f"a memory repository cannot cut '{level.field}' to a {level.grain}; "
                    "a date grain is answered by the database adapter"
                )
        self._refuse_unanswerable(
            aggregate,
            [level.field for level in grouping.group_by]
            + [measure.field for measure in grouping.measures.values()],
        )
        rows, _ = self._matching(aggregate, criteria, handed_back=False)
        where, _ = self._filterable(aggregate).accept(criteria.expression)
        return _tree(rows, grouping, criteria, tuple(grouping.group_by), meta, where)

    def group_by(
        self, target: type, by: Sequence[str], criteria: Criteria | None = None
    ) -> list[dict[str, Any]]:
        """The matches folded by these fields, one flat row per combination with its count.

            repository.group_by(Invoice, ["state", "currency"])
            ->  [{"state": "open", "currency": "BOB", "count": 12}, …]

        The same tree `group_by_levels` builds, with every leaf flattened to a row.
        """
        criteria = criteria or Criteria()
        levels = tuple(Level(field=field) for field in by)
        buckets = self._grouped(target, criteria, Grouping(group_by=levels))
        return [
            dict(zip(by, path)) | {"count": bucket.count}
            for path, bucket in _flattened_buckets(buckets)
        ]

    # ---------------------------------------------------------------- writing

    def group_by_levels(self, target: type, criteria: Criteria | None = None) -> list[Bucket]:
        """The matches folded as a tree, the way `criteria.grouping` describes it — levels,
        measures, having, order, page and filled axes — answered in memory over the records
        held. A grouping that names no level is refused: it would be a count, and asking for
        it as a grouping is a mistake. A date grain is refused too (see `_grouped`).
        """
        criteria = criteria or Criteria()
        if not criteria.grouping.asked:
            raise exceptions.InvalidCriteria(
                "grouping by nothing answers one bucket holding everything, which is a count "
                "and not a grouping; name at least one field in criteria.grouping"
            )
        return self._grouped(target, criteria, criteria.grouping)

    def _kept(self, event: Any) -> bool:
        """Whether this event is already held — by its id, under any class it is an instance
        of — so an event recorded twice is written once.
        """
        return any(
            event.id in held for kind, held in self._stored.items() if isinstance(event, kind)
        )

    def _events_of_sourced(self, records: list[Any]) -> list[Any]:
        """An event-sourced entity stands for the events it recorded since it was rebuilt; one
        whose next version another writer already appended is refused, as the database does.
        """
        written: list[Any] = []
        for one in records:
            if not isinstance(one, EventSourcedMixin):
                written.append(one)
                continue
            for event in one.unsaved_events():
                if self._kept(event):
                    continue
                taken = {
                    other.entity_version
                    for other in self._events_about(type(one), one.id)
                    if other.id != event.id
                }
                if event.entity_version in taken:
                    raise exceptions.StaleAggregate(
                        f"{type(one).__name__} changed since it was rebuilt: another writer "
                        "appended its next event first; rebuild it and try again"
                    )
                written.append(event)
        return written

    def _acting(self) -> str | None:
        """Read per save and never raises: a write with nobody behind it is `None`, not a
        failure — the same bargain the engine makes."""
        if self._actor is None:
            return None
        try:
            return self._actor()
        except Exception:
            return None

    def _stamp(self, record: Any, is_new: bool) -> None:
        """Who wrote it, for an aggregate that has somewhere to put it.

        The engine does this on the flush, where it reaches every write. Here there is only
        `save()`, so that is where it goes — the point being that an aggregate tested against
        this double comes back stamped the way it will be in production, instead of carrying a
        `None` that only ever appears in tests.
        """
        acting = self._acting()
        if acting is None:
            return
        if is_new and getattr(record, "created_by", "") is None:
            record.created_by = acting
        if not is_new and hasattr(record, "updated_by"):
            record.updated_by = acting

    def _save_one(self, record: Any) -> None:
        """One record written into the store, with what the engine stamps on a save.

        1. The block in play, if any, keeps the record as it was and marks it saved.
        2. For an `Entity`:
           2.1 another object held under the same id with a different version is refused as
               `StaleAggregate` — somebody saved since this one was read;
           2.2 `updated_at` stamped now on an update, or set to `created_at` on a first save
               that has none;
           2.3 `created_by` / `updated_by` stamped from `actor`;
           2.4 the version raised by one.
        Final: the record itself held under its id — the caller's object, not a copy.
        """
        stored = self._stored.setdefault(type(record), {})
        identity = identity_of(record)
        self._touching(type(record), identity)
        if isinstance(record, Entity):
            held = stored.get(identity)
            if held is not None and held is not record and held.version != record.version:
                raise exceptions.StaleAggregate(
                    f"{type(record).__name__} changed since it was read; "
                    "read it again before writing"
                )
            if held is not None:
                record.updated_at = utc_now()
            elif record.updated_at is None:
                record.updated_at = record.created_at
            self._stamp(record, held is None)
            record.version += 1
        stored[identity] = record

    def _keep_recorded(self, records: list[Any]) -> None:
        """What the aggregates written recorded, kept beside them — read, never taken, and each
        once — the way the database keeps them in the context's event table."""
        for one in records:
            if isinstance(one, DomainEvent) or not hasattr(one, "recorded_events"):
                continue
            for event in one.recorded_events():
                if not self._kept(event):
                    self._save_one(event)

    def save(self, record: Any) -> None:
        """Keeps one aggregate or several, raising the version the way the adapter does, and
        refusing a write whose version the stored record has already moved past.

        **Every `before_save` first, then the writes, then every `after_save`** — the shape the
        engine has, where one flush follows all of them. Interleaved per record, a batch whose
        third aggregate is refused left the first two written and their `after_save` already
        fired, while the engine wrote nothing at all. A test against this double would see
        records that production never stored.

            repository.save(Invoice(id="F-1"))            ->  None; stored at version 1
            invoice = repository.get(Invoice, "F-1"); invoice.total = 900
            repository.save(invoice)                      ->  None; version 2, updated_at now
            repository.save(stale_copy_at_version_1)      ->  StaleAggregate

        What the aggregates recorded is kept beside them, each event once, between the writes
        and the `after_save` hooks.
        """
        refuse_writing_in_preview("save")
        self._refuse_reentrant_write()
        records = self._events_of_sourced(records_of(record))
        newness = self._before_writes(records)
        for one in records:
            self._save_one(one)
        self._keep_recorded(records)
        self._after_writes(records, newness)

    def _forget(self, record: Any) -> None:
        """One record deleted from the store, the block in play keeping it as it was so a
        rollback puts it back.
        """
        self._touching(type(record), identity_of(record))
        self._stored.setdefault(type(record), {}).pop(identity_of(record), None)

    def remove(self, record: Any) -> None:
        """Deletes one aggregate or several, archivable or not.

        Three passes, for the reason `save` gives: a batch is refused whole or applied whole.

            repository.remove([invoice_1, invoice_2])   ->  None; neither held any more
            repository.get(Invoice, invoice_1.id)       ->  None

        No cascade here: the children a root owns stay held unless removed with it. Inside
        `context()` a raise puts the removed back.
        """
        refuse_writing_in_preview("remove")
        self._refuse_reentrant_write()
        records = records_of(record)
        for one in records:
            self._before_remove(one)
        for one in records:
            self._forget(one)
        for one in records:
            self._after_remove(one)

    def extended(
        self, root: type, *extensions_: type, delegated: bool = False
    ) -> "MemoryRepository":
        """Says what extends `root` in this store, the way `map_aggregates` does for a database:
        each class a subclass of it, with the fields it adds. A `Feature` tested here then reads,
        creates and edits through the commands of `root` as it would against the engine.

            repository = MemoryRepository().extended(Move, MoveDelivery, MoveTracking)

        `delegated=True` writes the answers of `root` with each extension under its own key.
        """
        for extension in extensions_:
            inherited = {one.name for one in dataclasses.fields(extension.__mro__[1])}
            extensions.register(
                root,
                extension,
                tuple(
                    one.name
                    for one in dataclasses.fields(extension)
                    if one.name not in inherited
                ),
            )
        if delegated:
            extensions.DELEGATED.add(root)
        return self

    def extend(self, record: Any, extension: type) -> None:
        """Gives a stored record the class of an extension: the record held is replaced by an
        instance of `extension` with the same fields, and the fields it adds at their defaults. A
        store of objects keeps what a class adds on the object, so there is no row to insert.

            repository.extend(move, MoveDelivery)   ->  None
            repository.get(Move, move.id)           ->  MoveDelivery(id=…, route=None)
            repository.extend(unsaved_move, MoveDelivery)   ->  None, nothing held to extend

        1. The store holding the record, as its class or an extension of it — nothing done
           when none holds it, or when it already is the extension.
        2. An instance of `extension` built from the record's init fields, the rest of the
           extension's at their defaults.
        3. Every field of the record copied onto it, the ones `__init__` does not take too.
        4. Both keys marked touched, so a unit of work in play can put them back.
        Final: the record taken out of its class's store and the instance kept under the
        extension, same identity.
        """
        held = self._held_as(type(record))
        identity = identity_of(record)
        stored = next((rows for rows in held if identity in rows), None)
        if stored is None or isinstance(record, extension):
            return
        promoted = extension(
            **{
                one.name: getattr(record, one.name)
                for one in dataclasses.fields(extension)
                if one.init and hasattr(record, one.name)
            }
        )
        for one in dataclasses.fields(record):
            setattr(promoted, one.name, getattr(record, one.name))
        self._touching(type(record), identity)
        self._touching(extension, identity)
        self._stored[type(record)].pop(identity, None)
        self._stored.setdefault(extension, {})[identity] = promoted

    def reclassify(self, entity: type) -> int:
        """A store of objects keeps the type of a record in the record: there is none to write."""
        return 0

    def archive(self, record: Any) -> None:
        """Stamps when it left and keeps the row — one aggregate or several.

            repository.archive(partner)          ->  None; partner.archived_at set, still held
            repository.get(Partner, partner.id)  ->  None, an archived record answers nothing
            repository.archive(invoice_line)     ->  ProgrammingError, not `ArchivableMixin`

        Each `before_archive` and `archive()`, then one `save` of all — with its hooks and
        version check — then each `after_archive`, as the engine does.
        """
        refuse_writing_in_preview("archive")
        self._refuse_reentrant_write()
        records = records_of(record)
        refuse_unarchivable(records)
        for one in records:
            self._before_archive(one)
            one.archive()
        self.save(records)
        for one in records:
            self._after_archive(one)

    def _matched_for_writing(self, model: type, criteria: Criteria, verb: str) -> list[Any]:
        """The records a write by criteria reaches, refused as the engine refuses it: a page
        cannot bound it, and a condition the aggregate cannot answer would widen it."""
        if criteria.pagination.asked:
            raise exceptions.ProgrammingError(
                f"{verb} writes every record its filter matches; a page cannot bound it"
            )
        rows, dropped = self._matching(model, criteria, handed_back=False)
        unanswered = [one.field for one in dropped if one.reason != "not_expandable"]
        if unanswered:
            raise exceptions.ProgrammingError(
                f"{verb} cannot answer {', '.join(unanswered)} on {model.__name__}; a write "
                "never runs wider than it was asked"
            )
        return rows

    def update_all(self, target: type, criteria: Criteria, values: Mapping[str, Any]) -> int:
        """The engine's `update_all`, over the records held: values set, version raised,
        `updated_at` stamped, no hook.

            expired = Criteria(where={"field": "state", "operator": "=", "value": "expired"})
            repository.update_all(Session, expired, {"state": "closed"})   ->  3
            repository.update_all(Session, expired, {"version": 9})
                ->  ProgrammingError("update_all cannot set version on Session: …")

        1. Refused in a preview and inside another write's hook.
        2. Refused when a value names something that is not a field, a relation, or one the
           framework keeps — the identity, `created_at`, `version`, `updated_at`.
        3. The records the filter reaches — refused for a page, or a condition the aggregate
           cannot answer.
        4. Each one marked touched, its values set, and an entity's version raised and
           `updated_at` stamped with one moment for all.
        Final: how many records it changed.
        """
        refuse_writing_in_preview("update_all")
        self._refuse_reentrant_write()
        model, _ = model_and_collection(target)
        meta = self.definition(model)
        fixed = {meta.identity, "created_at", "version", "updated_at"}
        wrong = sorted(
            name
            for name in values
            if name not in meta.fields
            or name in fixed
            or meta.fields[name].type.is_relational
        )
        if wrong:
            raise exceptions.ProgrammingError(
                f"update_all cannot set {', '.join(wrong)} on {model.__name__}: not a field, "
                "or one the framework keeps"
            )
        rows = self._matched_for_writing(model, criteria, "update_all")
        now = utc_now()
        for one in rows:
            self._touching(type(one), identity_of(one))
            for name, value in values.items():
                setattr(one, name, value)
            if isinstance(one, Entity):
                one.version += 1
                one.updated_at = now
        return len(rows)

    def remove_all(self, target: type, criteria: Criteria) -> int:
        """The engine's `remove_all`, over the records held: no hook, no cascade.

            expired = Criteria(where={"field": "state", "operator": "=", "value": "expired"})
            repository.remove_all(Session, expired)   ->  3, none of them held any more

        Refused for a page, or a condition the aggregate cannot answer — as the engine.
        """
        refuse_writing_in_preview("remove_all")
        self._refuse_reentrant_write()
        model, _ = model_and_collection(target)
        rows = self._matched_for_writing(model, criteria, "remove_all")
        for one in rows:
            self._forget(one)
        return len(rows)

    def upsert(
        self, record: Any, on: Sequence[str], update: Sequence[str] | None = None
    ) -> Upserted:
        """The engine's `upsert`, over the records held: one per key, the last one; a held
        record with the same key is overwritten and its version raised, a new one is kept as a
        copy — the records handed in are not refreshed, as the engine leaves them. Answers the
        same counts the engine does.

            repository.upsert([Rate("USD", 6.96), Rate("EUR", 7.5)], on=["currency"])
                ->  Upserted(written=2, skipped=0)        both new, kept at version 1
            repository.upsert([Rate("USD", 6.97)], on=["currency"])
                ->  Upserted(written=1, skipped=0)        the USD held overwritten, version 2
            repository.upsert([Rate("USD", 6.98)], on=["currency"], update=[])
                ->  Upserted(written=0, skipped=1)        a conflict with nothing to overwrite

        1. Refused in a preview, inside another write's hook, and when `update` names a field
           the framework keeps.
        2. One record per key, the last one — a key with a `None` in it is its own.
        3. `before_save` for each record kept.
        4. Per record:
           4.1 refused when `on` or `update` names a field its class does not have;
           4.2 the held record with the same key values — none when the key has a `None`;
           4.3 none held but its identity taken by another key: `DuplicateAggregate`;
           4.4 none held: a copy kept, at version 1 when it had none — written;
           4.5 held: the fields to overwrite — the ones named, or every one but the key, the
               framework's own, `archived_at` and `created_by` — set on it, its version raised
               and `updated_at` stamped; written, unless there is nothing to overwrite.
        Final: `after_save` for each, and how many were written and skipped.
        """
        refuse_writing_in_preview("upsert")
        self._refuse_reentrant_write()
        records = records_of(record)
        kept_by_framework = sorted(
            {"id", "created_at", "version", "updated_at"} & set(update or ())
        )
        if kept_by_framework:
            raise exceptions.ProgrammingError(
                f"upsert cannot overwrite {', '.join(kept_by_framework)}: the framework keeps it"
            )
        latest: dict[Any, Any] = {}
        for one in records:
            values = tuple(getattr(one, name) for name in on)
            # A key with a NULL in it never conflicts, as in the database.
            latest[(type(one), *values) if None not in values else ("unkeyed", id(one))] = one
        for one in latest.values():
            self._fire("before_save", one)
        written = 0
        for one in latest.values():
            model = type(one)
            meta = self.definition(model)
            unknown = [name for name in [*on, *(update or ())] if name not in meta.fields]
            if unknown:
                raise exceptions.ProgrammingError(
                    f"{model.__name__} has no field {', '.join(unknown)}"
                )
            keyed = None not in tuple(getattr(one, name) for name in on)
            held = next(
                (
                    stored
                    for stored in self._rows(model)
                    if keyed
                    and all(getattr(stored, name) == getattr(one, name) for name in on)
                ),
                None,
            )
            twin = self._stored.get(model, {}).get(identity_of(one))
            if held is None and twin is not None:
                raise exceptions.DuplicateAggregate(
                    f"{model.__name__} {identity_of(one)} is already stored under another "
                    f"{', '.join(on)}"
                )
            if held is None:
                kept = copy(one)
                if isinstance(kept, Entity) and not kept.version:
                    kept.version = 1
                    kept.updated_at = kept.updated_at or kept.created_at
                self._touching(model, identity_of(kept))
                self._stored.setdefault(model, {})[identity_of(kept)] = kept
                written += 1
                continue
            fixed = {meta.identity, "created_at", "version", "updated_at", *on}
            fixed |= {"archived_at", "created_by"}
            overwritten = (
                list(update)
                if update is not None
                else [n for n in meta.fields if n not in fixed]
            )
            if not overwritten:
                continue
            self._touching(type(held), identity_of(held))
            for name in overwritten:
                setattr(held, name, getattr(one, name))
            if isinstance(held, Entity):
                held.version += 1
                held.updated_at = utc_now()
            written += 1
        for one in latest.values():
            self._fire("after_save", one)
        return Upserted(written=written, skipped=len(latest) - written)
