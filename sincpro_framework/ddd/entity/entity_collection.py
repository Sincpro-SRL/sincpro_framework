"""A set of records, and what it is honest about.

The collection *is* the page: it carries its records plus the cursor and the count. Two rules
make that safe — a derived collection carries no metadata, and a partial one refuses to fold
itself. See `docs/persistence/reference.md`.
"""

from collections.abc import Callable, Hashable, Iterable, Iterator
from dataclasses import dataclass, fields, is_dataclass
from functools import partial
from types import GenericAlias
from typing import TYPE_CHECKING, Any, get_args, get_origin

from pydantic import GetCoreSchemaHandler
from pydantic_core import CoreSchema, core_schema

from sincpro_framework.ddd import exceptions
from sincpro_framework.ddd.criteria import Evaluation
from sincpro_framework.sincpro_abstractions import DataTransferObject

if TYPE_CHECKING:
    from sincpro_framework.ddd.criteria import Expression, Specification
    from sincpro_framework.ddd.entity.entity_meta import Meta


class Count(DataTransferObject):
    """How many exist in total.

    `exact=False` means *at least* `value`: counting stops at a ceiling so its cost does not
    grow with the table. A client renders that as `10000+`, and a collection reads it as
    "you are holding a fragment".
    """

    value: int
    exact: bool

    def __str__(self) -> str:
        """`"42"` when exact, `"10000+"` when it is a floor."""
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

        identity_name(Dataset)     ->  "dataset_id"    # its first field, dataclass or model
        identity_name(Invoice)     ->  "id"            # an Entity's first field is `id`
        identity_name(int)         ->  raises TypeError("int declares no identity")

    Context: a class that is neither a dataclass nor a model with fields has no identity to
    read, and saying so beats keying records by something arbitrary.
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

        identity_of(Dataset(dataset_id="ds_1", ...))   ->  "ds_1"
        identity_of(Invoice(id="0192…", ...))          ->  "0192…"
        identity_of("ds_1")                            ->  "ds_1"   # not a record: itself

    A value that is neither a dataclass nor a model is its own identity — an id already.
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
        raise exceptions.ProgrammingError(
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
        """How many in each bucket, never the records: `Changes(added=2, removed=1, …)`."""
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
        """How many records are held — this page, never the total (`count` is that)."""
        return len(self.items)

    def __iter__(self) -> Iterator[T]:
        """The records held, in order."""
        return iter(self.items)

    def __bool__(self) -> bool:
        """Whether any record is held — an empty page is falsy even when `count` says more."""
        return bool(self.items)

    def __getitem__(self, index: int) -> T:
        """The record at this position of the page."""
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

    def _fold(self, name: str, selector: Callable[[T], Any]) -> list[Any]:
        """The values an aggregation is about, once it is established that it may answer.

        complete   EntityCollection([a(2), b(3)]) → [2, 3]
        partial    a page of 20 out of 8412 → ProgrammingError, naming `self.repository.measures(...)`
        """
        if self.is_partial:
            raise exceptions.ProgrammingError(
                f"{name} would answer about {len(self.items)} records out of "
                f"{self.count or 'more'}; ask the engine instead, which folds the whole "
                f"result set: self.repository.measures(<aggregate>, criteria, total=('sum', '<field>'))"
            )
        return [selector(record) for record in self.items]

    @classmethod
    def holds(cls) -> type | None:
        """Context: which aggregate this collection is of, read off the generic parameter the
        subclass already declares. `None` for the plain `EntityCollection`, which holds anything.

            Datasets.holds()           ->  Dataset
            EntityCollection.holds()   ->  None
            # class Datasets(EntityCollection[Dataset])

        A subclass of `Datasets` that declares no parameter of its own answers `Dataset` too:
        it reads the generic bases it inherits.
        """
        for base in getattr(cls, "__orig_bases__", ()):
            if get_origin(base) is EntityCollection or base is EntityCollection:
                declared = get_args(base)
                if declared and isinstance(declared[0], type):
                    return declared[0]
        return None

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
            in      EntityCollection([a, b])   out  ProgrammingError: expected exactly one record, found 2

        Holding a set and needing one element is real, and `collection[0]` is how it gets
        handled silently — including the day a filter that was unique stops being.
        """
        if len(self.items) != 1:
            raise exceptions.ProgrammingError(
                f"expected exactly one record, found {len(self.items)}"
            )
        return self.items[0]

    def filtered(self, predicate: Callable[[T], bool]) -> "EntityCollection[T]":
        """The records this predicate accepts, in order.

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
        selects of itself is what the database would have selected. Values are compared as the
        field's own type: a condition written as text goes through `Meta.accept` first.

        >>> page.filtered_by(criteria.expression).ids
        ['ds_01b2…']
        """
        program = Evaluation.program(expression)
        return self._derived(
            record for record in self.items if program(partial(getattr, record))
        )

    def partition(
        self, predicate: Callable[[T], bool]
    ) -> tuple["EntityCollection[T]", "EntityCollection[T]"]:
        """Those that match and those that do not, in one pass and in order.

            EntityCollection([a(1), b(9), c(2)]).partition(lambda x: x.size < 5)
                ->  (EntityCollection([a, c]), EntityCollection([b]))

        Both halves are derived collections, as `filtered` answers: no cursor, no count.
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
            in      {"code": {}}    →  out  [{'id': …, 'code': '1010'}, …]   identity included

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

    @classmethod
    def whole(cls, records: Iterable[T]) -> "EntityCollection[T]":
        """The collection of these records, all there are: counted exactly, no cursor — what a
        list given to a field declared `EntityCollection[…]` is held as.

            EntityCollection.whole([a, b])   ->  EntityCollection([a, b], Count(2, exact=True))
        """
        items = tuple(records)
        return cls(items=items, count=Count(value=len(items), exact=True))

    @classmethod
    def of_page(cls, page: "EntityCollection[T]") -> "EntityCollection[T]":
        """`page` — its records, cursor, count, what was dropped and its definition — as this
        class: what a field declared `Lines` holds when a reading answered a plain collection.
        """
        return cls(
            items=page.items,
            cursor=page.cursor,
            count=page.count,
            dropped=page.dropped,
            meta=page.meta,
        )

    def _edited(self, items: Iterable[T], moved: int) -> "EntityCollection[T]":
        """This same set, edited: the records changed, everything else it says kept — its
        cursor, its definition, what was dropped — and its count moved by `moved` when it was
        exact. A floor stays a floor: «at least 1000» plus one is still «at least».

        Context: an edit is not a derivation. `filtered` answers another set, which is no page
        of anything; `append` answers the same set with one more, so a page stays a page —
        appending to forty of a thousand never makes it look whole.
        """
        count = self.count
        if count is not None and count.exact:
            count = Count(value=max(count.value + moved, 0), exact=True)
        return type(self)(
            items=tuple(items),
            cursor=self.cursor,
            count=count,
            dropped=self.dropped,
            meta=self.meta,
        )

    def append(self, *records: T) -> "EntityCollection[T]":
        """A new collection with `records` at the end; this one never changes — as `str.replace`
        and `date.replace` answer anew.

            invoice.lines = invoice.lines.append(InvoiceLine(qty=2))
            EntityCollection([a, b], count=Count(2)).append(c)   ->  [a, b, c], Count(3)
        """
        return self._edited([*self.items, *records], len(records))

    def extend(self, records: Iterable[T]) -> "EntityCollection[T]":
        """A new collection with every one of `records` at the end, in order.

        EntityCollection([a]).extend([b, c])   ->  EntityCollection([a, b, c])
        """
        return self.append(*records)

    def remove(self, *records: Any) -> "EntityCollection[T]":
        """A new collection without these records — each named by itself or by its identity.
        Like `list.remove`, one that is not held is a `ValueError`, naming it.

            invoice.lines = invoice.lines.remove(line)        # or remove(line.id)
            EntityCollection([a, b, c]).remove(b)   ->  EntityCollection([a, c])
        """
        held = {identity_of(record) for record in self.items}
        unwanted = [
            (
                one
                if not hasattr(one, "__dataclass_fields__")
                and not hasattr(one, "model_fields")
                else identity_of(one)
            )
            for one in records
        ]
        missing = [one for one in unwanted if one not in held]
        if missing:
            raise ValueError(f"not in the collection: {', '.join(map(str, missing))}")
        gone = set(unwanted)
        kept = [record for record in self.items if identity_of(record) not in gone]
        return self._edited(kept, -len(gone))

    def replace(self, *records: T) -> "EntityCollection[T]":
        """A new collection where each of `records` takes the place of the held one with its
        identity, in that same position. One whose identity is not held is a `ValueError`:
        `append` adds.

            invoice.lines = invoice.lines.replace(replace(line, qty=5))
        """
        by_identity = {identity_of(record): record for record in records}
        held = {identity_of(record) for record in self.items}
        missing = [key for key in by_identity if key not in held]
        if missing:
            raise ValueError(
                f"not in the collection: {', '.join(map(str, missing))} — append adds a record"
            )
        return self._edited(
            [by_identity.get(identity_of(record), record) for record in self.items], 0
        )

    def __class_getitem__(cls, held: Any) -> Any:
        """`EntityCollection[Invoice]`, as `list[Invoice]` is: a `types.GenericAlias`, which hands
        what is asked of it to the class — so pydantic finds the collection's own schema
        (`__get_pydantic_core_schema__`) for a parametrized one too, where `typing`'s alias hid it
        and pydantic read the collection's fields instead."""
        return GenericAlias(cls, held)

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        """A collection as a field of a DTO or an entity: read from the collection itself, from
        a list of records — the whole set, counted —, or from the page a wire sent
        (`{items, count, cursor}`); written as that page.

            ResponseInvoices(invoices=[i1, i2]).invoices   ->  EntityCollection([i1, i2], Count(2))
            on the wire                                    ->  {"items": [...], "count": {...}, "cursor": null}
        """
        declared = get_args(source)
        held = (
            declared[0] if declared else (cls.holds() if isinstance(source, type) else None)
        )
        kind = get_origin(source) or source
        if not (isinstance(kind, type) and issubclass(kind, EntityCollection)):
            kind = cls
        item = handler.generate_schema(held) if held is not None else core_schema.any_schema()
        count = core_schema.nullable_schema(handler.generate_schema(Count))
        cursor = core_schema.nullable_schema(core_schema.str_schema())

        def whole(records: list[Any]) -> EntityCollection[Any]:
            return kind.whole(records)

        def sent(page: dict[str, Any]) -> EntityCollection[Any]:
            return kind(
                items=tuple(page["items"]), count=page.get("count"), cursor=page.get("cursor")
            )

        def written(value: Any) -> dict[str, Any]:
            """The page a field holds, as the wire writes it — a list held in its place (a
            draft, a record built in memory) written as the whole collection it is."""
            if not isinstance(value, EntityCollection):
                value = kind.whole(value or ())
            return {"items": list(value.items), "count": value.count, "cursor": value.cursor}

        page = core_schema.typed_dict_schema(
            {
                "items": core_schema.typed_dict_field(core_schema.list_schema(item)),
                "count": core_schema.typed_dict_field(count, required=False),
                "cursor": core_schema.typed_dict_field(cursor, required=False),
            }
        )

        def as_declared(held: EntityCollection[Any]) -> EntityCollection[Any]:
            """A collection of another class held as the one the field declares."""
            return held if isinstance(held, kind) else kind.of_page(held)

        return core_schema.union_schema(
            [
                core_schema.is_instance_schema(kind),
                core_schema.no_info_after_validator_function(
                    as_declared, core_schema.is_instance_schema(EntityCollection)
                ),
                core_schema.no_info_after_validator_function(
                    whole, core_schema.list_schema(item)
                ),
                core_schema.no_info_after_validator_function(sent, page),
            ],
            serialization=core_schema.plain_serializer_function_ser_schema(
                written, return_schema=page
            ),
        )

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
