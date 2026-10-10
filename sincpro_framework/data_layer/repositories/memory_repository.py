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

Three things it does not have: relations, units of work and date grains, which is where
a database starts — and `remove` here deletes, the way it does there; `archive` is the other one.
"""

import dataclasses
from collections.abc import Callable, Iterator, Mapping, Sequence
from copy import copy
from typing import Any, overload

from sincpro_framework.ddd.criteria import (
    Bucket,
    Condition,
    CountMode,
    Criteria,
    Grouping,
    Level,
    Measure,
    Operator,
    Sort,
    combined,
    conditions_of,
    parse_order,
)
from sincpro_framework.ddd.criteria.evaluate import matches
from sincpro_framework.ddd.criteria.pagination import CursorKeys
from sincpro_framework.ddd.entity import ArchivableMixin, Entity, utc_now
from sincpro_framework.ddd.entity.entity_collection import (
    Count,
    Dropped,
    EntityCollection,
    identity_name,
    identity_of,
    model_and_collection,
)
from sincpro_framework.ddd.entity.entity_meta import Meta, describe_class
from sincpro_framework.ddd.entity.mixins.event_sourced import EventSourcedMixin
from sincpro_framework.ddd.events.domain_event import NAME, DomainEvent
from sincpro_framework.ddd.events.mixins.deliverable import (
    DELIVERY_FIELDS,
    DeliverableEventMixin,
)
from sincpro_framework.ddd.exceptions import (
    ContractViolation,
    DuplicateAggregate,
    InvalidCriteria,
    StaleAggregate,
)
from sincpro_framework.ddd.repositories.capabilities import (
    Analyzes,
    StoreCapabilities,
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
    return ordered[below] + (ordered[above] - ordered[below]) * (at - below)


MEASURES: dict[str, Callable[..., Any]] = {
    "count": lambda values: len(values),
    "count_distinct": lambda values: len(set(values)),
    "sum": lambda values: sum(values),
    "min": lambda values: min(values, default=None),
    "max": lambda values: max(values, default=None),
    "avg": lambda values: (sum(values) / len(values)) if values else None,
    "percentile": _percentile,
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
    flat = []
    for bucket in buckets:
        here = path + (bucket.value,)
        flat.extend(
            _flattened_buckets(bucket.groups, here) if bucket.groups else [(here, bucket)]
        )
    return flat


def _measured(rows: list[Any], grouping: Grouping) -> dict[str, Any]:
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


def _tree(
    rows: list[Any],
    grouping: Grouping,
    criteria: Criteria,
    fields: tuple[str, ...],
    meta: Meta,
) -> list[Bucket]:
    """One level of groups and the levels below it, with the same having, order and page the
    database would apply."""
    if not fields:
        return []
    field, rest = fields[0], fields[1:]
    by_value: dict[Any, list[Any]] = {}
    for record in rows:
        by_value.setdefault(getattr(record, field), []).append(record)

    buckets = []
    for value, mine in by_value.items():
        opens = criteria.merged_with(
            Criteria(where=Condition(field=field, operator=Operator.EQ, value=value))
        ).model_copy(update={"grouping": Grouping()})
        measures = _measured(mine, grouping)
        if grouping.where_measures is not None and not matches(
            _Measured(count=len(mine), **measures), grouping.where_measures
        ):
            continue
        ids, cursor = _page_of_ids(mine, criteria, meta)
        buckets.append(
            Bucket(
                field=field,
                value=value,
                count=len(mine),
                measures=measures,
                criteria=opens,
                ids=ids,
                cursor=cursor,
                groups=_tree(mine, grouping, opens, rest, meta),
            )
        )
    return _ordered_buckets(buckets, grouping)


def _page_of_ids(
    rows: list[Any], criteria: Criteria, meta: Meta
) -> tuple[list[Any], str | None]:
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


def _ordered_buckets(buckets: list[Bucket], grouping: Grouping) -> list[Bucket]:
    if not grouping.order:
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


def _bucket_key(bucket: Bucket, name: str) -> Any:
    if name == "count":
        return bucket.count
    if name in bucket.measures:
        return bucket.measures[name]
    return (bucket.value is None, str(bucket.value))


class _Measured:
    """What `having` is evaluated against: the numbers a group folded, as attributes."""

    def __init__(self, **values: Any) -> None:
        self.__dict__.update(values)


class MemoryRepository(ChangeTrackingRepositoryMixin, IRepository, Analyzes, WritesInBulk):
    """Every read and write the vocabulary can answer without a database. No `Transacts`: a
    test that needs a unit of work runs on SQLite."""

    @property
    def capabilities(self) -> StoreCapabilities:
        """Percentiles, computed the way a database does; no locks and no savepoints, because
        there is no transaction to hold them."""
        return StoreCapabilities(percentiles=True)

    def __init__(
        self,
        *records: Any,
        hooks: Hooks | None = None,
        actor: "Callable[[], str | None] | None" = None,
    ) -> None:
        """`actor` answers who is writing, for an `AuditedMixin` aggregate — the same argument
        `Database` takes and read the same way, once per save. Without one those fields stay
        `None`, which is also what the engine does."""
        refuse_wiring_as_a_record(records)
        self._actor = actor
        super().__init__(hooks)
        self._stored: dict[type, dict[Any, Any]] = {}
        self.add(*records)

    def add(self, *records: Any) -> "MemoryRepository":
        """Seeds it, without the version check a save makes: how a test arranges its world."""
        for record in records:
            self._stored.setdefault(type(record), {})[identity_of(record)] = record
        return self

    def definition(self, aggregate: type) -> Meta:
        """What may be asked about this aggregate, read off its annotations."""
        return describe_class(aggregate, identity_name(aggregate))

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
        return list(self._stored.setdefault(aggregate, {}).values())

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
        expression, dropped = self._filterable(aggregate).accept(criteria.expression)
        _, unanswerable = meta.accept_specification(criteria.specification)
        rows = [
            self._read(record) if handed_back else record
            for record in self._rows(aggregate)
            if matches(record, expression)
        ]
        if self._live(aggregate, criteria):
            rows = [record for record in rows if not record.is_archived]
        return rows, dropped + unanswerable

    def _ordering(self, aggregate: type, criteria: Criteria) -> tuple[Sort, ...]:
        meta = self.definition(aggregate)
        sorts = criteria.order or parse_order(meta.default_order)
        for sort in sorts:
            meta.orderable(sort.field)
        if any(sort.field == meta.identity for sort in sorts):
            return sorts
        return sorts + (Sort(field=meta.identity, descending=sorts[0].descending),)

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
        """
        refuse_locking(for_update or skip_locked or nowait)
        aggregate, _holder = model_and_collection(target)
        if detail is not None:
            self.definition(aggregate).accept_specification(detail.specification)
        if isinstance(aggregate, type) and issubclass(aggregate, EventSourcedMixin):
            return self._read(
                aggregate.rebuilt(identity, self._events_about(aggregate, identity))
            )
        note_read(aggregate)
        found = self._stored.setdefault(aggregate, {}).get(identity)
        if found is not None and isinstance(found, ArchivableMixin) and found.is_archived:
            return None
        return self._read(found)

    @overload
    def browse[C: EntityCollection](self, target: type[C], ids: Sequence[Any]) -> C: ...

    @overload
    def browse[T](self, target: type[T], ids: Sequence[Any]) -> EntityCollection[T]: ...

    def browse(self, target: type, ids: Sequence[Any]) -> Any:
        aggregate, holder = model_and_collection(target)
        note_read(aggregate)
        stored = self._stored.setdefault(aggregate, {})
        found = [self._read(stored[key]) for key in ids if key in stored]
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
        """The page this criteria asks for, with its cursor, its count and what was dropped."""
        refuse_locking(for_update or skip_locked or nowait)
        criteria = criteria or Criteria()
        aggregate, holder = model_and_collection(target)
        # Matched without reading: the page is cut first, and only what the caller is handed
        # goes through `after_read`. Read here, a paged walk would run the hook once per row per
        # page — seven rows over three pages is twenty-one — while the engine, which fetches
        # only the page, runs it seven times.
        rows, dropped = self._matching(aggregate, criteria, handed_back=False)
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
        """Every record the criteria matches, as one complete collection."""
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
        criteria = criteria or Criteria()
        while True:
            page = self.search(target, criteria)
            yield page
            if page.cursor is None:
                return
            criteria = criteria.resuming_from(page.cursor)

    def count(self, target: type, criteria: Criteria | None = None) -> Count:
        aggregate, _holder = model_and_collection(target)
        rows, _ = self._matching(aggregate, criteria or Criteria(), handed_back=False)
        return Count(value=len(rows), exact=True)

    def exists(self, target: type, criteria: Criteria | None = None) -> bool:
        return self.count(target, criteria).value > 0

    @overload
    def first[T](
        self, target: type[EntityCollection[T]], criteria: Criteria | None = None
    ) -> T | None: ...

    @overload
    def first[T](self, target: type[T], criteria: Criteria | None = None) -> T | None: ...

    def first(self, target: type, criteria: Criteria | None = None) -> Any:
        return self.search(target, criteria).first()

    @overload
    def one[T](
        self, target: type[EntityCollection[T]], criteria: Criteria | None = None
    ) -> T: ...

    @overload
    def one[T](self, target: type[T], criteria: Criteria | None = None) -> T: ...

    def one(self, target: type, criteria: Criteria | None = None) -> Any:
        return self.fetch_all(target, criteria).ensure_one()

    @overload
    def get_by[T](self, target: type[EntityCollection[T]], **values: Any) -> T | None: ...

    @overload
    def get_by[T](self, target: type[T], **values: Any) -> T | None: ...

    def get_by(self, target: type, **values: Any) -> Any:
        """One record by a natural key: the values that identify it besides its id."""
        if not values:
            raise InvalidCriteria("get_by needs at least one value, e.g. code='1010'")
        found = self.fetch_all(target, _by(values))
        if len(found) > 1:
            named = ", ".join(f"{field}={value!r}" for field, value in values.items())
            raise ContractViolation(
                f"{len(found)} records answer to {named}; a natural key names one"
            )
        return found.first()

    def pluck(self, target: type, field: str, criteria: Criteria | None = None) -> list[Any]:
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
        """
        if not measures:
            raise InvalidCriteria(
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

    def group_by(
        self, target: type, by: Sequence[str], criteria: Criteria | None = None
    ) -> list[dict[str, Any]]:
        criteria = criteria or Criteria()
        levels = tuple(Level(field=field) for field in by)
        buckets = self._grouped(target, criteria, Grouping(group_by=levels))
        return [
            dict(zip(by, path)) | {"count": bucket.count}
            for path, bucket in _flattened_buckets(buckets)
        ]

    def group_by_levels(self, target: type, criteria: Criteria | None = None) -> list[Bucket]:
        criteria = criteria or Criteria()
        if not criteria.grouping.asked:
            raise InvalidCriteria(
                "grouping by nothing answers one bucket holding everything, which is a count "
                "and not a grouping; name at least one field in criteria.grouping"
            )
        return self._grouped(target, criteria, criteria.grouping)

    def _grouped(self, target: type, criteria: Criteria, grouping: Grouping) -> list[Bucket]:
        """The levels the grouping names, split in memory. A grain is the adapter's: cutting a
        date to a month is SQL a database writes, and a double that guessed it would be a
        second implementation to keep in step."""
        aggregate, holder = model_and_collection(target)
        meta = self.definition(aggregate)
        for level in grouping.group_by:
            meta.field(level.field)
            if level.grain is not None:
                raise ContractViolation(
                    f"a memory repository cannot cut '{level.field}' to a {level.grain}; "
                    "a date grain is answered by the database adapter"
                )
        rows, _ = self._matching(aggregate, criteria, handed_back=False)
        return _tree(
            rows, grouping, criteria, tuple(level.field for level in grouping.group_by), meta
        )

    # ---------------------------------------------------------------- writing

    def save(self, record: Any) -> None:
        """Keeps one aggregate or several, raising the version the way the adapter does, and
        refusing a write whose version the stored record has already moved past.

        **Every `before_save` first, then the writes, then every `after_save`** — the shape the
        engine has, where one flush follows all of them. Interleaved per record, a batch whose
        third aggregate is refused left the first two written and their `after_save` already
        fired, while the engine wrote nothing at all. A test against this double would see
        records that production never stored.
        """
        refuse_writing_in_preview("save")
        self._refuse_reentrant_write()
        records = self._events_of_sourced(records_of(record))
        newness = self._before_writes(records)
        for one in records:
            self._save_one(one)
        self._keep_recorded(records)
        self._after_writes(records, newness)

    def _kept(self, event: Any) -> bool:
        return any(
            event.id in held for kind, held in self._stored.items() if isinstance(event, kind)
        )

    def _events_about(self, aggregate: type, identity: Any) -> list[DomainEvent]:
        return [
            event
            for event in self._rows(DomainEvent)
            if event.entity_type == aggregate.__name__ and event.entity_id == identity
        ]

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
                    raise StaleAggregate(
                        f"{type(one).__name__} changed since it was rebuilt: another writer "
                        "appended its next event first; rebuild it and try again"
                    )
                written.append(event)
        return written

    def _keep_recorded(self, records: list[Any]) -> None:
        """What the aggregates written recorded, kept beside them — read, never taken, and each
        once — the way the database keeps them in the context's event table."""
        for one in records:
            if isinstance(one, DomainEvent) or not hasattr(one, "recorded_events"):
                continue
            for event in one.recorded_events():
                if not self._kept(event):
                    self._save_one(event)

    def _save_one(self, record: Any) -> None:
        stored = self._stored.setdefault(type(record), {})
        identity = identity_of(record)
        if isinstance(record, Entity):
            held = stored.get(identity)
            if held is not None and held is not record and held.version != record.version:
                raise StaleAggregate(
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

    def remove(self, record: Any) -> None:
        """Deletes one aggregate or several, archivable or not.

        Three passes, for the reason `save` gives: a batch is refused whole or applied whole.
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

    def archive(self, record: Any) -> None:
        """Stamps when it left and keeps the row — one aggregate or several."""
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

    def _acting(self) -> str | None:
        """Read per save and never raises: a write with nobody behind it is `None`, not a
        failure — the same bargain the engine makes."""
        if self._actor is None:
            return None
        try:
            return self._actor()
        except Exception:
            return None

    def _forget(self, record: Any) -> None:
        self._stored.setdefault(type(record), {}).pop(identity_of(record), None)

    def _matched_for_writing(self, model: type, criteria: Criteria, verb: str) -> list[Any]:
        """The records a write by criteria reaches, refused as the engine refuses it: a page
        cannot bound it, and a condition the aggregate cannot answer would widen it."""
        if criteria.pagination.asked:
            raise ContractViolation(
                f"{verb} writes every record its filter matches; a page cannot bound it"
            )
        rows, dropped = self._matching(model, criteria, handed_back=False)
        unanswered = [one.field for one in dropped if one.reason != "not_expandable"]
        if unanswered:
            raise ContractViolation(
                f"{verb} cannot answer {', '.join(unanswered)} on {model.__name__}; a write "
                "never runs wider than it was asked"
            )
        return rows

    def update_all(self, target: type, criteria: Criteria, values: Mapping[str, Any]) -> int:
        """The engine's `update_all`, over the records held: values set, version raised,
        `updated_at` stamped, no hook."""
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
            raise ContractViolation(
                f"update_all cannot set {', '.join(wrong)} on {model.__name__}: not a field, "
                "or one the framework keeps"
            )
        rows = self._matched_for_writing(model, criteria, "update_all")
        now = utc_now()
        for one in rows:
            for name, value in values.items():
                setattr(one, name, value)
            if isinstance(one, Entity):
                one.version += 1
                one.updated_at = now
        return len(rows)

    def remove_all(self, target: type, criteria: Criteria) -> int:
        """The engine's `remove_all`, over the records held: no hook, no cascade."""
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
        same counts the engine does."""
        refuse_writing_in_preview("upsert")
        self._refuse_reentrant_write()
        records = records_of(record)
        kept_by_framework = sorted(
            {"id", "created_at", "version", "updated_at"} & set(update or ())
        )
        if kept_by_framework:
            raise ContractViolation(
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
                raise ContractViolation(f"{model.__name__} has no field {', '.join(unknown)}")
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
                raise DuplicateAggregate(
                    f"{model.__name__} {identity_of(one)} is already stored under another "
                    f"{', '.join(on)}"
                )
            if held is None:
                kept = copy(one)
                if isinstance(kept, Entity) and not kept.version:
                    kept.version = 1
                    kept.updated_at = kept.updated_at or kept.created_at
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
            for name in overwritten:
                setattr(held, name, getattr(one, name))
            if isinstance(held, Entity):
                held.version += 1
                held.updated_at = utc_now()
            written += 1
        for one in latest.values():
            self._fire("after_save", one)
        return Upserted(written=written, skipped=len(latest) - written)
