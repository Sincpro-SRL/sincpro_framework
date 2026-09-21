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

from collections.abc import Callable, Iterable, Iterator, Sequence
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
from sincpro_framework.ddd.entity.model_meta import Meta, describe_class
from sincpro_framework.ddd.exceptions import (
    ContractViolation,
    InvalidCriteria,
    StaleAggregate,
)
from sincpro_framework.ddd.repositories.change_tracking import ChangeTrackingRepositoryMixin
from sincpro_framework.ddd.repositories.hooks import Rule
from sincpro_framework.ddd.repositories.repository import (
    Repository,
    records_of,
    refuse_locking,
    refuse_unarchivable,
    refuse_wiring_as_a_record,
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


class MemoryRepository(ChangeTrackingRepositoryMixin, Repository):
    """Every read and write the vocabulary can answer without a database."""

    def __init__(
        self,
        *records: Any,
        rules: Sequence[Rule] | None = None,
        hooks: Iterable[Any] | None = None,
        deps: Any = None,
        actor: "Callable[[], str | None] | None" = None,
    ) -> None:
        """`actor` answers who is writing, for an `AuditedMixin` aggregate — the same argument
        `Database` takes and read the same way, once per save. Without one those fields stay
        `None`, which is also what the engine does."""
        refuse_wiring_as_a_record(records)
        self._actor = actor
        super().__init__(rules, hooks, deps)
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

    def _rows(self, aggregate: type) -> list[Any]:
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
        expression, dropped = meta.accept(criteria.expression)
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
    ) -> T | None:
        """One record by its identity, or `None`; an archived one answers `None` too."""
        refuse_locking(for_update)
        aggregate, _holder = model_and_collection(target)
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
    ) -> C: ...

    @overload
    def search[T](
        self,
        target: type[T],
        criteria: Criteria | None = None,
        for_update: bool = False,
        skip_locked: bool = False,
    ) -> EntityCollection[T]: ...

    def search(
        self,
        target: type,
        criteria: Criteria | None = None,
        for_update: bool = False,
        skip_locked: bool = False,
    ) -> Any:
        """The page this criteria asks for, with its cursor, its count and what was dropped."""
        refuse_locking(for_update)
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
        self._refuse_reentrant_write()
        records = records_of(record)
        newness = self._before_writes(records)
        for one in records:
            self._save_one(one)
        self._after_writes(records, newness)

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
            self._stamp(record, held is None)
            record.version += 1
        stored[identity] = record

    def remove(self, record: Any) -> None:
        """Deletes one aggregate or several, archivable or not.

        Three passes, for the reason `save` gives: a batch is refused whole or applied whole.
        """
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
