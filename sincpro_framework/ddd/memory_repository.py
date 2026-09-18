"""A repository with no database behind it: the same vocabulary, answered over records held in
memory.

    accounts = MemoryRepository(Account(code="1010", …), Account(code="2010", …))
    bus.add_dependency("repository", accounts)

What it is for is testing a Feature without a database and without a fake: the filter is
answered by `matches`, the same evaluator the SQL translator is checked against, so a double
that passes here agrees with the engine by construction. What it is not for is production; it
holds everything it was given and offers no transaction.

Three things it deliberately does the way the adapter does, because a Feature can see them:
the version is raised on every save and a stale one is refused, `updated_at` is stamped, and an
`ArchivableMixin` aggregate is archived by `remove` and left out of a reading that did not ask for
it. Three it does not have: relations, units of work and date grains, which is where a database
starts.
"""

from collections.abc import Callable, Iterator, Sequence
from typing import Any

from sincpro_framework.ddd.criteria import (
    FOLD_FUNCTIONS,
    Bucket,
    Condition,
    CountMode,
    Criteria,
    Grouping,
    Level,
    Operator,
    Sort,
    combined,
    conditions_of,
    parse_order,
)
from sincpro_framework.ddd.entity import ArchivableMixin, Entity, utc_now
from sincpro_framework.ddd.entity_collection import (
    Count,
    Dropped,
    identity_name,
    identity_of,
    model_and_collection,
)
from sincpro_framework.ddd.evaluate import matches
from sincpro_framework.ddd.exceptions import (
    ContractViolation,
    InvalidCriteria,
    StaleAggregate,
)
from sincpro_framework.ddd.model_meta import Meta, describe_class
from sincpro_framework.ddd.pagination import CursorKeys

FOLDS: dict[str, Callable[[list[Any]], Any]] = {
    "count": len,
    "sum": lambda values: sum(values),
    "min": lambda values: min(values, default=None),
    "max": lambda values: max(values, default=None),
    "avg": lambda values: (sum(values) / len(values)) if values else None,
}


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


class MemoryRepository:
    """Every read and write the vocabulary can answer without a database."""

    def __init__(self, *records: Any) -> None:
        self._stored: dict[type, dict[Any, Any]] = {}
        self.add(*records)

    # ---------------------------------------------------------------- what it holds

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
        self, aggregate: type, criteria: Criteria
    ) -> tuple[list[Any], list[Dropped]]:
        meta = self.definition(aggregate)
        expression, dropped = meta.accept(criteria.expression)
        _, unanswerable = meta.accept_specification(criteria.specification)
        rows = [record for record in self._rows(aggregate) if matches(record, expression)]
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

    # ---------------------------------------------------------------- reading

    def get(self, target: type, identity: Any, **_: Any) -> Any:
        """One record by its identity, or `None`; an archived one answers `None` too."""
        aggregate, _holder = model_and_collection(target)
        found = self._stored.setdefault(aggregate, {}).get(identity)
        if found is not None and isinstance(found, ArchivableMixin) and found.is_archived:
            return None
        return found

    def browse(self, target: type, ids: Sequence[Any]) -> Any:
        aggregate, holder = model_and_collection(target)
        stored = self._stored.setdefault(aggregate, {})
        found = [stored[key] for key in ids if key in stored]
        return holder(
            items=tuple(found),
            count=Count(value=len(found), exact=True),
            meta=self.definition(aggregate),
        )

    def search(self, target: type, criteria: Criteria | None = None, **_: Any) -> Any:
        """The page this criteria asks for, with its cursor, its count and what was dropped."""
        criteria = criteria or Criteria()
        aggregate, holder = model_and_collection(target)
        rows, dropped = self._matching(aggregate, criteria)
        sorts = self._ordering(aggregate, criteria)
        ordered = _sorted(rows, sorts)

        keys = criteria.pagination.keys_for(sorts)
        walked = _after(ordered, keys, sorts) if keys is not None else ordered
        walked = walked[criteria.pagination.skipped() :]
        kept = walked[: criteria.limit]
        has_more = len(walked) > criteria.limit

        return holder(
            items=tuple(kept),
            cursor=(
                criteria.pagination.next_from(kept[-1], sorts) if has_more and kept else None
            ),
            count=(
                None
                if criteria.count is CountMode.NONE
                else Count(value=len(rows), exact=True)
            ),
            dropped=tuple(dropped),
            meta=self.definition(aggregate) if criteria.meta else None,
        )

    def fetch_all(self, target: type, criteria: Criteria | None = None) -> Any:
        """Every record the criteria matches, as one complete collection."""
        criteria = criteria or Criteria()
        aggregate, holder = model_and_collection(target)
        rows, dropped = self._matching(aggregate, criteria)
        ordered = _sorted(rows, self._ordering(aggregate, criteria))
        return holder(
            items=tuple(ordered),
            count=Count(value=len(ordered), exact=True),
            dropped=tuple(dropped),
            meta=self.definition(aggregate) if criteria.meta else None,
        )

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
        rows, _ = self._matching(aggregate, criteria or Criteria())
        return Count(value=len(rows), exact=True)

    def exists(self, target: type, criteria: Criteria | None = None) -> bool:
        return self.count(target, criteria).value > 0

    def first(self, target: type, criteria: Criteria | None = None) -> Any:
        return self.search(target, criteria).first()

    def one(self, target: type, criteria: Criteria | None = None) -> Any:
        return self.fetch_all(target, criteria).ensure_one()

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
        rows, _ = self._matching(aggregate, criteria or Criteria())
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

    def totals(
        self, target: type, criteria: Criteria | None = None, **folds: str
    ) -> dict[str, Any]:
        if not folds:
            raise InvalidCriteria("totals needs at least one fold, e.g. rows='sum:row_count'")
        aggregate, _holder = model_and_collection(target)
        meta = self.definition(aggregate)
        rows, _ = self._matching(aggregate, criteria or Criteria())
        answer = {}
        for name, fold in folds.items():
            function, _, field = fold.partition(":")
            if not field:
                raise InvalidCriteria(
                    f"'{name}={fold}' should read as 'function:field', e.g. 'sum:row_count'"
                )
            if function not in FOLD_FUNCTIONS:
                raise InvalidCriteria(
                    f"'{function}' is not a fold; use one of {', '.join(FOLD_FUNCTIONS)}"
                )
            meta.field(field)
            values = [
                getattr(record, field)
                for record in rows
                if getattr(record, field) is not None
            ]
            answer[name] = FOLDS[function](values)
        return answer

    def group_by(
        self, target: type, by: Sequence[str], criteria: Criteria | None = None
    ) -> list[dict[str, Any]]:
        criteria = criteria or Criteria()
        levels = tuple(Level(field=field) for field in by)
        buckets = self._grouped(target, criteria, Grouping(by=levels))
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
        for level in grouping.by:
            meta.field(level.field)
            if level.grain is not None:
                raise ContractViolation(
                    f"a memory repository cannot cut '{level.field}' to a {level.grain}; "
                    "a date grain is answered by the database adapter"
                )
        rows, _ = self._matching(aggregate, criteria)
        return _tree(
            rows, grouping, criteria, tuple(level.field for level in grouping.by), meta
        )

    # ---------------------------------------------------------------- writing

    def save(self, record: Any) -> None:
        """Keeps one aggregate, raising its version the way the adapter does, and refusing a
        write whose version the stored record has already moved past."""
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
            record.version += 1
        stored[identity] = record

    def save_all(self, records: Sequence[Any]) -> None:
        for record in records:
            self.save(record)

    def remove(self, record: Any) -> None:
        """Archives an `ArchivableMixin`, deletes anything else."""
        if isinstance(record, ArchivableMixin):
            record.archive()
            self.save(record)
            return
        self.purge(record)

    def remove_all(self, records: Sequence[Any]) -> None:
        for record in records:
            self.remove(record)

    def purge(self, record: Any) -> None:
        self._stored.setdefault(type(record), {}).pop(identity_of(record), None)


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


def _folded(rows: list[Any], grouping: Grouping) -> dict[str, Any]:
    return {
        name: FOLDS[fold.function](
            [
                getattr(record, fold.field)
                for record in rows
                if getattr(record, fold.field) is not None
            ]
        )
        for name, fold in grouping.totals.items()
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
        totals = _folded(mine, grouping)
        if grouping.having is not None and not matches(
            _Folded(count=len(mine), **totals), grouping.having
        ):
            continue
        ids, cursor = _page_of_ids(mine, criteria, meta)
        buckets.append(
            Bucket(
                field=field,
                value=value,
                count=len(mine),
                totals=totals,
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
    if name in bucket.totals:
        return bucket.totals[name]
    return (bucket.value is None, str(bucket.value))


class _Folded:
    """What `having` is evaluated against: the numbers a group folded, as attributes."""

    def __init__(self, **values: Any) -> None:
        self.__dict__.update(values)
