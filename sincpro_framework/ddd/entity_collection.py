"""A set of records, and what it is honest about.

The collection *is* the page: it carries its records plus the cursor and the count. Two rules
make that safe — a derived collection carries no metadata, and a partial one refuses to fold
itself. See `docs/persistence/reference.md`.
"""

from collections.abc import Callable, Hashable, Iterable, Iterator
from dataclasses import dataclass, fields, is_dataclass
from typing import TYPE_CHECKING, Any, get_args, get_origin

from sincpro_framework.ddd.exceptions import ContractViolation
from sincpro_framework.sincpro_abstractions import DataTransferObject

if TYPE_CHECKING:
    from sincpro_framework.ddd.criteria import Expression, Specification
    from sincpro_framework.ddd.model_meta import Meta


class Count(DataTransferObject):
    """How many exist in total.

    `exact=False` means *at least* `value`: counting stops at a ceiling so its cost does not
    grow with the table. A client renders that as `10000+`, and a collection reads it as
    "you are holding a fragment".
    """

    value: int
    exact: bool

    def __str__(self) -> str:
        return str(self.value) if self.exact else f"{self.value}+"


class Dropped(DataTransferObject):
    """A condition that was asked for and could not be honoured.

    Reported rather than raised: a shared link outlives the schema it was written against, and
    a dropped filter always *widens* the result — so it is dropped and said out loud.
    """

    field: str
    reason: str


def identity_name(aggregate: type) -> str:
    """The name of the identity field: the first one declared, the convention `identity_of`
    reads by, for a dataclass or a pydantic model alike.

    >>> identity_name(Dataset)
    'dataset_id'
    """
    if is_dataclass(aggregate):
        return fields(aggregate)[0].name
    model_fields = getattr(aggregate, "model_fields", None)
    if model_fields:
        return next(iter(model_fields))
    raise TypeError(f"{aggregate.__name__} declares no identity")


def identity_of(record: Any) -> Any:
    """Context: the first field a record declares is its identity — a convention, so nothing
    has to be registered. Works over a dataclass and a pydantic model alike, because making
    those two share a base class would be a migration demanded by a convenience.

    >>> identity_of(Dataset(dataset_id="ds_1", ...))
    'ds_1'
    """
    if is_dataclass(record):
        return getattr(record, fields(record)[0].name)
    model_fields = getattr(type(record), "model_fields", None)
    if model_fields:
        return getattr(record, next(iter(model_fields)))
    return record


def model_and_collection(target: type) -> tuple[type, type]:
    """What was asked for, as the pair everything here works with.

        in      Dataset                 the mapped class
        out     (Dataset, EntityCollection)   the plain collection holds anything

        in      Datasets                class Datasets(EntityCollection[Dataset])
        out     (Dataset, Datasets)     the model read off the generic parameter

    A collection subclass is optional, so both spellings work.
    """
    if not isinstance(target, type) or not issubclass(target, EntityCollection):
        return target, EntityCollection

    held = target.holds()
    if held is None:
        raise ContractViolation(
            f"{target.__name__} does not say which aggregate it holds; "
            "declare it as EntityCollection[TheAggregate]"
        )
    return held, target


def _declared(record: Any) -> list[str]:
    """The names a record declares, dataclass or pydantic alike."""
    if is_dataclass(record):
        return [declared.name for declared in fields(record)]
    return list(getattr(type(record), "model_fields", {}))


def _flattened(record: Any, specification: "Specification | None") -> dict[str, Any]:
    """One record as a dictionary, cut by the mask and read attribute by attribute.

    Nothing the mask did not name is read, so a relation the criteria never asked for is not
    touched — and therefore does not raise.
    """
    if specification is None:
        return {name: getattr(record, name) for name in _declared(record)}

    identity = identity_name(type(record))
    written: dict[str, Any] = {identity: getattr(record, identity)}
    for name, node in specification.root.items():
        value = getattr(record, name, None)
        if node.specification is None:
            written[name] = value
        elif isinstance(value, EntityCollection):
            written[name] = value.to_records(node.specification)
        elif isinstance(value, list):
            written[name] = [_flattened(one, node.specification) for one in value]
        elif value is None:
            written[name] = None
        else:
            written[name] = _flattened(value, node.specification)
    return written


@dataclass(frozen=True, slots=True)
class Changes[T]:
    """What one collection has that another does not, by identity.

        in      what is stored now, against what the import brought
        out     Changes(added=2, removed=1, changed=3, unchanged=94)

    The four buckets are disjoint and every record of both sides is in exactly one, so a
    synchronisation writes `added` and `changed` and archives `removed` without counting twice.
    """

    added: tuple[T, ...] = ()
    """In this collection, absent from the other: what has to be written."""
    removed: tuple[T, ...] = ()
    """In the other, absent from this one: what is gone."""
    changed: tuple[tuple[T, T], ...] = ()
    """Same identity on both sides, different content, as `(mine, theirs)`."""
    unchanged: tuple[T, ...] = ()

    def __repr__(self) -> str:
        return (
            f"Changes(added={len(self.added)}, removed={len(self.removed)}, "
            f"changed={len(self.changed)}, unchanged={len(self.unchanged)})"
        )

    @property
    def any(self) -> bool:
        """Whether anything at all differs.

        >>> page.changes_against(page).any
        False
        """
        return bool(self.added or self.removed or self.changed)


@dataclass(frozen=True, slots=True, repr=False)
class EntityCollection[T]:
    """A page of records plus the metadata of where they came from.

    **A dataclass and not a `DataTransferObject`, which is the exception to the house rule and
    the reason is concrete:** a collection is a sequence — it iterates its records — and
    pydantic's `BaseModel` already defines `__iter__` to walk its own fields. Overriding it
    breaks `dict(model)` and every pydantic internal that relies on it.

    `Cursor` and `Count` beside it are DTOs, because neither is a sequence.
    """

    items: tuple[T, ...] = ()
    cursor: str | None = None
    """Where the next page starts. `None` means no next one, or that this is not a page."""

    count: Count | None = None
    """How many exist in total, capped unless somebody asked otherwise. `None` means nobody
    asked, never that there are none."""

    dropped: tuple[Dropped, ...] = ()
    """Conditions that were asked for and could not be honoured; see `Dropped`."""

    meta: "Meta | None" = None
    """What the model these records came from looks like — its fields, their types, what may
    be filtered and ordered. The engine read it to answer, so it travels with the answer and
    nobody describes a model twice. `None` on a collection built by hand."""

    def __repr__(self) -> str:
        """Context: a summary and never the records — a collection travels as a Feature's
        response and the bus logs responses, so a faithful repr writes whole pages of data into
        the log and from there into traces.

        >>> repr(page)
        'EntityCollection(20 records of 197, more)'
        """
        return (
            f"{type(self).__name__}({len(self.items)} records"
            f"{'' if self.count is None else f' of {self.count}'}"
            f"{', more' if self.cursor else ''}"
            f"{f', {len(self.dropped)} dropped' if self.dropped else ''})"
        )

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self) -> Iterator[T]:
        return iter(self.items)

    def __bool__(self) -> bool:
        return bool(self.items)

    def __getitem__(self, index: int) -> T:
        return self.items[index]

    def __or__(self, other: "EntityCollection[T]") -> "EntityCollection[T]":
        """Everything in either, left order first, no repeats.

        in      EntityCollection([a, b]) | EntityCollection([b, c])
        out     EntityCollection([a, b, c])
        """
        seen = {identity_of(record) for record in self.items}
        extra = [record for record in other if identity_of(record) not in seen]
        return self._derived(list(self.items) + extra)

    def __and__(self, other: "EntityCollection[T]") -> "EntityCollection[T]":
        """Everything in both, in this collection's order.

        in      EntityCollection([c, a, b]) & EntityCollection([b, a])
        out     EntityCollection([a, b])
        """
        wanted = {identity_of(record) for record in other}
        return self._derived(r for r in self.items if identity_of(r) in wanted)

    def __sub__(self, other: "EntityCollection[T]") -> "EntityCollection[T]":
        """Everything here that is not there, in order.

        in      EntityCollection([a, b, c]) - EntityCollection([b])
        out     EntityCollection([a, c])
        """
        unwanted = {identity_of(record) for record in other}
        return self._derived(r for r in self.items if identity_of(r) not in unwanted)

    def __xor__(self, other: "EntityCollection[T]") -> "EntityCollection[T]":
        """In one or the other but not both.

        in      EntityCollection([a, b]) ^ EntityCollection([b, c])
        out     EntityCollection([a, c])
        """
        return (self - other) | (other - self)

    def _derived(self, items: Iterable[T]) -> "EntityCollection[T]":
        """Another collection of this same kind, holding these records and no metadata.

            in      EntityCollection(items=(a, b), cursor="…", count=Count(197)), [a]
            out     EntityCollection(items=(a,), cursor=None, count=None)

        Context: the load-bearing line of this module. What comes out is not a page of
        anything, so it has no next page, no total and no report — a leftover cursor is one
        somebody would use to ask for the next page of a set that no longer exists.

        `type(self)` and not `EntityCollection`, so a subclass stays itself through a `filtered`.
        The definition of the model stays too: it describes the records, not the page.
        """
        return type(self)(items=tuple(items), meta=self.meta)

    def _fold(self, name: str, selector: Callable[[T], Any]) -> list[Any]:
        """The values an aggregation is about, once it is established that it may answer.

        complete   EntityCollection([a(2), b(3)]) → [2, 3]
        partial    a page of 20 out of 8412 → ContractViolation, naming `self.repository.measures(...)`
        """
        if self.is_partial:
            raise ContractViolation(
                f"{name} would answer about {len(self.items)} records out of "
                f"{self.count or 'more'}; ask the engine instead, which folds the whole "
                f"result set: self.repository.measures(<aggregate>, criteria, total=('sum', '<field>'))"
            )
        return [selector(record) for record in self.items]

    @classmethod
    def holds(cls) -> type | None:
        """Context: which aggregate this collection is of, read off the generic parameter the
        subclass already declares. `None` for the plain `EntityCollection`, which holds anything.

        >>> Datasets.holds()      # class Datasets(EntityCollection[Dataset])
        <class 'Dataset'>
        """
        for base in getattr(cls, "__orig_bases__", ()):
            if get_origin(base) is EntityCollection or base is EntityCollection:
                declared = get_args(base)
                if declared and isinstance(declared[0], type):
                    return declared[0]
        return None

    @property
    def is_partial(self) -> bool:
        """Whether these records are a fragment of what the criteria matched.

            cursor="…"                        →  True    there is another page
            count=None                        →  False   nobody asked, nothing claimed
            count=Count(10000, exact=False)   →  True     a floor is a fragment
            count=Count(2, exact=True), 2 held→  False    everything is in hand


        1. Holding a cursor means there is another page — the cheapest answer.
        2. No count means nobody asked, so nothing here claims to be a fragment.
        3. Final: a floor is a fragment by definition, and so is an exact total larger than
           what is held.
        """
        if self.cursor is not None:
            return True
        if self.count is None:
            return False
        return not self.count.exact or self.count.value > len(self.items)

    @property
    def ids(self) -> list[Any]:
        """The identity of every record held, in order.

        in      EntityCollection([ds_a, ds_b])
        out     ['ds_01a0…', 'ds_01b2…']
        """
        return [identity_of(record) for record in self.items]

    def first(self) -> T | None:
        """The first record held, or `None` when there are none.

        in  EntityCollection([a, b])  →  out  a
        in  EntityCollection([])      →  out  None
        """
        return self.items[0] if self.items else None

    def last(self) -> T | None:
        """The last record held, or `None` when there are none.

        in  EntityCollection([a, b])  →  out  b
        """
        return self.items[-1] if self.items else None

    def ensure_one(self) -> T:
        """The single record held, or a refusal saying how many there actually were.

            in      EntityCollection([a])      out  a
            in      EntityCollection([a, b])   out  ContractViolation: expected exactly one record, found 2

        Holding a set and needing one element is real, and `collection[0]` is how it gets
        handled silently — including the day a filter that was unique stops being.
        """
        if len(self.items) != 1:
            raise ContractViolation(f"expected exactly one record, found {len(self.items)}")
        return self.items[0]

    def filtered(self, predicate: Callable[[T], bool]) -> "EntityCollection[T]":
        """The records this predicate keeps, in order.

            in      EntityCollection([ds_a(rows=10), ds_b(rows=900)]), lambda d: d.row_count > 100
            out     EntityCollection([ds_b])          no cursor, no count — it is not a page

        >>> page.filtered(lambda dataset: dataset.is_synthetic()).ids
        ['ds_01a0…', 'ds_01b2…']
        """
        return self._derived(record for record in self.items if predicate(record))

    def filtered_by(self, expression: "Expression | None") -> "EntityCollection[T]":
        """The records that answer this filter, in order — the same filter a caller sends.

            in      EntityCollection([ds_a(rows=10), ds_b(rows=900)]), Condition(row_count, GT, 100)
            out     EntityCollection([ds_b])          no cursor, no count — it is not a page

        Answered in memory by the same rules the engine translates to SQL, so what a page
        keeps of itself is what the database would have kept. Values are compared as the
        field's own type: a condition written as text goes through `Meta.accept` first.

        >>> page.filtered_by(criteria.expression).ids
        ['ds_01b2…']
        """
        from sincpro_framework.ddd.evaluate import matches

        return self._derived(record for record in self.items if matches(record, expression))

    def partition(
        self, predicate: Callable[[T], bool]
    ) -> tuple["EntityCollection[T]", "EntityCollection[T]"]:
        """Those that match and those that do not, in one pass and in order.

        in      EntityCollection([a(1), b(9), c(2)]), lambda x: x.size < 5
        out     (EntityCollection([a, c]), EntityCollection([b]))
        """
        yes: list[T] = []
        no: list[T] = []
        for record in self.items:
            (yes if predicate(record) else no).append(record)
        return self._derived(yes), self._derived(no)

    def grouped(self, key: Callable[[T], Hashable]) -> dict[Hashable, "EntityCollection[T]"]:
        """These records in buckets, over what is held and nothing more.

            in      EntityCollection([a(size=1), b(size=1), c(size=2)]), lambda x: x.size
            out     {1: EntityCollection([a, b]), 2: EntityCollection([c])}

        Buckets the page, which is correct and is not the same operation as grouping the
        result set — that is `self.repository.group_by(...)` and happens in SQL.
        """
        buckets: dict[Hashable, list[T]] = {}
        for record in self.items:
            buckets.setdefault(key(record), []).append(record)
        return {value: self._derived(group) for value, group in buckets.items()}

    def mapped(self, field_or_selector: str | Callable[[T], Any]) -> list[Any]:
        """One value per record, by field name or by selector.

            in      EntityCollection([ds_a, ds_b]), "name"
            out     ['labs.csv', 'age_sex.csv']

        >>> page.mapped(lambda dataset: dataset.size_bytes // 1024)
        [148, 2]
        """
        if callable(field_or_selector):
            return [field_or_selector(record) for record in self.items]
        return [getattr(record, field_or_selector) for record in self.items]

    def index_by(self, field_or_selector: str | Callable[[T], Hashable]) -> dict[Hashable, T]:
        """The records by one value of theirs, the last one winning a tie.

            in      "code"                  →  out  {'1010': Account(…), '2010': Account(…)}
            in      lambda a: (a.kind, a.code)

        For the lookups a use case does over a page it already has: joining two readings by
        hand, checking what an import brought against what is stored.
        """
        read = (
            field_or_selector
            if callable(field_or_selector)
            else (lambda record: getattr(record, field_or_selector))
        )
        return {read(record): record for record in self.items}

    def to_records(
        self, specification: "Specification | None" = None
    ) -> list[dict[str, Any]]:
        """The records as dictionaries, masked the way the wire masks them.

            in      nothing asked   →  out  every field the record declares
            in      {"code": {}}    →  out  [{'id': …, 'code': '1010'}, …]   identity kept

        **Python values, not JSON**: a `datetime` stays a `datetime` and a `Decimal` a
        `Decimal`, because this is for what the process does next — a CSV, a comparison, a
        report — and not for a response, which `ResponsePaginatedQuery` writes.

        Only what the mask names is read, so a relation nobody asked for is never touched.
        """
        return [_flattened(record, specification) for record in self.items]

    def changes_against(
        self, other: "EntityCollection[T]", same: Callable[[T, T], bool] | None = None
    ) -> "Changes[T]":
        """What this collection has that the other does not, by identity.

            in      what an import brought, against what is stored
            out     Changes(added=2, removed=1, changed=3, unchanged=94)

        `same` decides what «changed» means and defaults to `==`. For an `Entity` that compares
        `version` and `updated_at` as well, so a synchronisation that only cares about the
        fields it owns passes its own comparison:

        >>> brought.changes_against(stored, same=lambda mine, theirs: mine.name == theirs.name)
        Changes(added=0, removed=0, changed=1, unchanged=24)
        """
        alike = same or (lambda mine, theirs: bool(mine == theirs))
        mine = {identity_of(record): record for record in self.items}
        theirs = {identity_of(record): record for record in other.items}
        added = tuple(record for key, record in mine.items() if key not in theirs)
        removed = tuple(record for key, record in theirs.items() if key not in mine)
        both = [(mine[key], theirs[key]) for key in mine if key in theirs]
        return Changes(
            added=added,
            removed=removed,
            changed=tuple((a, b) for a, b in both if not alike(a, b)),
            unchanged=tuple(a for a, b in both if alike(a, b)),
        )

    def sorted_by(
        self, key: Callable[[T], Any], descending: bool = False
    ) -> "EntityCollection[T]":
        """The same records in another order.

        in      EntityCollection([a(2), b(9), c(1)]), lambda x: x.size
        out     EntityCollection([c, a, b])
        """
        return self._derived(sorted(self.items, key=key, reverse=descending))

    def take(self, how_many: int) -> "EntityCollection[T]":
        """The first `how_many`, in order.

        in      EntityCollection([a, b, c]), 2   →   out  EntityCollection([a, b])
        """
        return self._derived(self.items[:how_many])

    def skip(self, how_many: int) -> "EntityCollection[T]":
        """Everything past the first `how_many`.

        in      EntityCollection([a, b, c]), 2   →   out  EntityCollection([c])
        """
        return self._derived(self.items[how_many:])

    def chunk(self, size: int) -> list["EntityCollection[T]"]:
        """Split into runs of at most `size`, in order.

        in      EntityCollection([a, b, c, d, e]), 2
        out     [EntityCollection([a, b]), EntityCollection([c, d]), EntityCollection([e])]
        """
        return [
            self._derived(self.items[start : start + size])
            for start in range(0, len(self.items), size)
        ]

    def count_where(self, predicate: Callable[[T], bool] | None = None) -> int:
        """How many are held, or how many of them match.

            in      EntityCollection([a(1), b(9), c(2)]), lambda x: x.size > 1
            out     2

        Never how many *exist* — that is `count`, and the name differs because the numbers do.

        >>> page.count_where()      # what is in hand        →  20
        >>> page.count              # what matched, in the database
        Count(value=197, exact=True)
        """
        if predicate is None:
            return len(self.items)
        return sum(1 for record in self.items if predicate(record))

    def sum_by(self, selector: Callable[[T], Any]) -> Any:
        """Adds a value across every record held — and only if every record is held.

        in      EntityCollection([a(2), b(3)]) complete, lambda x: x.size
        out     5
        but     a page of 20 out of 8412 raises: ask `self.repository.measures(...)` instead
        """
        return sum(self._fold("sum_by", selector))

    def average_by(self, selector: Callable[[T], Any]) -> float | None:
        """The mean across every record held, or `None` when there are none.

        in      EntityCollection([a(2), b(3)]), lambda x: x.size   →   out  2.5
        """
        values = self._fold("average_by", selector)
        return sum(values) / len(values) if values else None

    def min_by(self, selector: Callable[[T], Any]) -> T | None:
        """The record with the smallest value — the record, not the value.

        in      EntityCollection([a(2), b(3)]), lambda x: x.size   →   out  a
        """
        self._fold("min_by", selector)
        return min(self.items, key=selector) if self.items else None

    def max_by(self, selector: Callable[[T], Any]) -> T | None:
        """The record with the largest value — the record, not the value.

        in      EntityCollection([a(2), b(3)]), lambda x: x.size   →   out  b
        """
        self._fold("max_by", selector)
        return max(self.items, key=selector) if self.items else None
