"""A row read as a record of one entity: each name checked against its fields, each cell made what
its field reads, and each record it names marked to be looked up.

    shaped(Partner, {"name": "ACME", "vat": 1020304050, "country_id": "BO"})
        ->  {"name": "ACME", "vat": "1020304050",
             "country_id": Lookup(Country, by="code", key="BO")}

Context: a file names another record the three ways a person can — by its natural key
(`DEFAULT_GET_ID`), by the name another system gives it (`{"@key": …}`), or by its identity
(`{"id": …}`) — and which record that is, is only known once the store is asked. A `Lookup`
holds the question until `resolve` answers it with an identity. Everything else is left for
`apply_values`, which reads each value as its field's type exactly as a form's. What each field
holds is worked out once per entity (`slots_of`), not once per row.
"""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from functools import cache
from typing import Any

from sincpro_framework.ddd import exceptions
from sincpro_framework.ddd.entity import Entity, entity_id
from sincpro_framework.ddd.entity.entity_collection import identity_name
from sincpro_framework.ddd.entity.entity_meta import records_class, without_optional
from sincpro_framework.ddd.entity.relations import relation_attribute
from sincpro_framework.ddd.metamodel import model_of
from sincpro_framework.ddd.reads.reads import key_of

KEY = "@key"
"""The name a file gives the external key: of the row's record, or of the record a field names."""

NUMBERS = (int, float, Decimal)
"""The field types a decimal comma is read in."""


class Holds(StrEnum):
    """What a field of an entity holds, as an import reads it."""

    KEY = "key"
    """The key of another entity's record: `EntityID[Country]`."""
    KEYS = "keys"
    """The keys of several: `list[EntityID[Label]]`."""
    RECORDS = "records"
    """Records of another entity: the children it owns, or those a many-to-many names."""
    RECORD = "record"
    """One record of another entity, which a file names by the key field instead."""
    VALUE = "value"


@dataclass(frozen=True)
class Slot:
    """One field of an entity, as an import reads it."""

    holds: Holds
    annotation: Any
    target: type | None = None
    """The entity a key, a record or the records name."""
    by_id: bool = False
    """The records are named by id — a many-to-many the store manages —, never owned."""


@dataclass
class Lookup:
    """A record the file names by one of its keys, looked up when the batch is resolved."""

    entity: type
    by: str
    """`@key` for the external key, else the field it is found by: its natural key, or `id`."""
    key: str
    path: tuple[str | int, ...] = ()
    """Where the file names it, within the record: `("lines", 2, "product_id")`."""
    record: bool = False
    """Whether the field holds the record itself — a many-to-many — rather than its key."""
    identity: str = ""
    """The record's identity, once resolved."""


@cache
def slots_of(entity: type) -> dict[str, Slot]:
    """Each field of `entity` and what it holds — read off its annotations once.

    slots_of(Partner)["country_id"]   ->  Slot(Holds.KEY, …, target=Country)
    slots_of(Invoice)["lines"]        ->  Slot(Holds.RECORDS, …, target=InvoiceLine)
    slots_of(Ticket)["labels"]        ->  Slot(Holds.RECORDS, …, target=Label, by_id=True)
    """
    slots: dict[str, Slot] = {}
    for name, annotation in model_of(entity).annotations.items():
        key = entity_id.key_of(annotation)
        kind, many = records_class(annotation)
        if key is not None and key.entity is not None:
            slots[name] = Slot(Holds.KEYS if key.many else Holds.KEY, annotation, key.entity)
        elif isinstance(kind, type) and issubclass(kind, Entity):
            attribute = relation_attribute(entity, name)
            by_id = attribute is not None and attribute.named_by_id()
            slots[name] = Slot(
                Holds.RECORDS if many else Holds.RECORD, annotation, kind, by_id
            )
        else:
            slots[name] = Slot(Holds.VALUE, annotation)
    return slots


def is_blank(value: Any) -> bool:
    """Whether a cell holds nothing: empty, only spaces, or a nested value of blanks."""
    if isinstance(value, Mapping):
        return all(is_blank(one) for one in value.values())
    if isinstance(value, list):
        return all(is_blank(one) for one in value)
    return value is None or (isinstance(value, str) and not value.strip())


def lookups_in(value: Any) -> Iterator[Lookup]:
    """Every record a record's values name, its children's included."""
    if isinstance(value, Lookup):
        yield value
    elif isinstance(value, Mapping):
        for one in value.values():
            yield from lookups_in(one)
    elif isinstance(value, list):
        for one in value:
            yield from lookups_in(one)


def unknown_names(entity: type, values: Mapping[str, Any], path: str = "") -> list[str]:
    """The names a row holds that `entity` has no field for, as the header writes them.

    unknown_names(Partner, {"name": …, "colour": …})               ->  ["colour"]
    unknown_names(Invoice, {"lines": {"quantity": …, "colour": …}})   ->  ["lines/colour"]
    """
    slots = slots_of(entity)
    unknown: list[str] = []
    for name, value in values.items():
        if name == KEY and not path:
            continue
        slot = slots.get(name)
        if slot is None:
            unknown.append(f"{path}{name}")
        elif (
            slot.holds == Holds.RECORDS
            and slot.target is not None
            and isinstance(value, Mapping)
            and set(value) != {KEY}
        ):
            unknown.extend(unknown_names(slot.target, value, f"{path}{name}/"))
    return unknown


def _lookup(
    kind: type, value: Any, path: tuple[str | int, ...], record: bool = False
) -> Lookup | None:
    """The record a cell names: by the natural key of `kind` (`"BO"`), its external key
    (`{"@key": "base.bo"}`) or its identity (`{"id": "0192…"}`); `None` for a blank cell."""
    if is_blank(value):
        return None
    natural, identity = key_of(kind), identity_name(kind)
    if not isinstance(value, Mapping):
        return Lookup(kind, natural, str(value), path, record)
    named = [name for name in value if not is_blank(value[name])]
    if len(named) == 1 and named[0] in (KEY, identity, natural):
        return Lookup(kind, named[0], str(value[named[0]]), path, record)
    raise exceptions.InvalidValue(
        f"a {kind.__name__} is named by one key — {natural}, {KEY} or {identity} —, "
        f"not by {', '.join(named) or 'nothing'}",
        path,
    )


def _lookups(
    kind: type, value: Any, path: tuple[str | int, ...], record: bool = False
) -> list[Lookup]:
    """The records a cell names: a list, or one text with several keys separated by commas
    (`"vip,retail"`, `{"@key": "vip,retail"}`)."""
    if isinstance(value, list):
        items = value
    elif (
        isinstance(value, Mapping)
        and len(value) == 1
        and isinstance(next(iter(value.values())), str)
    ):
        name, text = next(iter(value.items()))
        items = [{name: one.strip()} for one in text.split(",")]
    elif isinstance(value, str):
        items = [one.strip() for one in value.split(",")]
    else:
        items = [value]
    found = [_lookup(kind, one, (*path, index), record) for index, one in enumerate(items)]
    return [one for one in found if one is not None]


def _scalar(annotation: Any, value: Any, decimal: str) -> Any:
    """A cell made what its field reads, the rest left for the field's type to read.

    1. A blank cell clears the field: an empty text for a text, `None` for anything else.
    2. A number in a text field is its text — Excel holds `1020304050` as a number.
    3. A moment in a date field is its day — Excel holds a date as a moment.
    4. A decimal comma is read as a point, the point as the thousands' separator.
    """
    if is_blank(value):
        return "" if annotation is str else None
    base = without_optional(annotation)
    if base is str and isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(int(value)) if float(value).is_integer() else str(value)
    if base is date and isinstance(value, datetime):
        return value.date()
    if base in NUMBERS and isinstance(value, str) and decimal != ".":
        return value.replace(".", "").replace(decimal, ".")
    return value


def shaped(
    entity: type,
    values: Mapping[str, Any],
    decimal: str = ".",
    path: tuple[str | int, ...] = (),
) -> dict[str, Any]:
    """A record's values as `apply_values` takes them, each record they name a `Lookup`.

        shaped(Partner, {"name": "ACME", "country_id": {"@key": "base.bo"}})
            ->  {"name": "ACME", "country_id": Lookup(Country, "@key", "base.bo")}
        shaped(Invoice, {"lines": [{"product_id": "SKU-7", "quantity": "2"}]})
            ->  {"lines": [{"product_id": Lookup(Product, "code", "SKU-7"), "quantity": "2"}]}
        shaped(Ticket, {"labels": "vip,retail"})
            ->  {"labels": [Lookup(Label, "name", "vip"), Lookup(Label, "name", "retail")]}

    1. A name that is not a field of `entity` is refused, pointing at it.
    2. A key to another entity (`EntityID[Country]`, `list[EntityID[Label]]`) is looked up.
    3. Records of another entity are looked up when the store names them by id (a many-to-many)
       or the cell names one by a key alone; otherwise each is a child the record owns, read as
       a record of its own.
    4. A to-one record is refused: the file names it by its key field.
    Final: every other value made what its field reads (`_scalar`).
    """
    slots = slots_of(entity)
    out: dict[str, Any] = {}
    for name, value in values.items():
        here = (*path, name)
        slot = slots.get(name)
        if slot is None:
            raise exceptions.InvalidValue(f"{entity.__name__} has no field {name}", here)
        target: type = slot.target or entity
        match slot.holds:
            case Holds.KEY:
                out[name] = _lookup(target, value, here)
            case Holds.KEYS:
                out[name] = _lookups(target, value, here)
            case Holds.RECORDS:
                items = (
                    value if isinstance(value, list) else [] if is_blank(value) else [value]
                )
                related: list[Any] = []
                for index, item in enumerate(items):
                    if slot.by_id or not isinstance(item, Mapping) or set(item) == {KEY}:
                        related.extend(_lookups(target, item, (*here, index), True))
                    elif not is_blank(item):
                        related.append(shaped(target, item, decimal, (*here, index)))
                out[name] = related
            case Holds.RECORD:
                raise exceptions.InvalidValue(
                    f"{entity.__name__}.{name} is another record: name it by its key field",
                    here,
                )
            case Holds.VALUE:
                out[name] = _scalar(slot.annotation, value, decimal)
    return out
