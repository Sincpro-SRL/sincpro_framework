"""Records written as the rows an import reads back — what Odoo calls an import-compatible export:
a person exports a set, edits it in a spreadsheet and imports the file again.

    layout_of(Invoice).header
        ->  ["id", "number", "partner_id", "lines/id", "lines/product_id", "lines/quantity"]
    rows_of(layout, [invoice], names)
        ->  ["0192…", "F-1", "ACME SRL", "0193…", "SKU-7", 2]
            [None,    None,  None,       "0194…", "SKU-9", 1]      the next line, in the row below

Context: the import finds a record by its identity, a field by its name, another record by its
natural key, and a child in the rows below its record (`imports.whole_records`). This writes
exactly that, from the same reading of the entity (`shaping.slots_of`), so what one writes the
other reads:

1. The identity comes first: importing the file again updates each record, never adds another.
2. What a form cannot edit is left out — a field the framework writes (`version`,
   `created_at`), one the database computes, one an `@compute` computes and nobody edits —: the
   import would refuse the first and compute the others again.
3. A key to another record is written as that record's natural key (`DEFAULT_GET_ID`) — `BO`,
   not `0192…` —, each looked up once per export (`find_names`); a many-to-many as its keys,
   separated by commas.
4. A child's key to the record that holds it is left out: the row already says whose it is.
5. A value one cell cannot hold — a list, a value object — is written as its JSON, which the
   import reads back.
"""

import json
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, time
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic_core import to_jsonable_python

from sincpro_framework.ddd.criteria import (
    Condition,
    CountMode,
    Criteria,
    Operator,
    Pagination,
    Specification,
)
from sincpro_framework.ddd.entity.entity_collection import identity_name, identity_of
from sincpro_framework.ddd.importing.shaping import Holds, Slot, slots_of
from sincpro_framework.ddd.metamodel import model_of
from sincpro_framework.ddd.reads.reads import key_of


@dataclass(frozen=True)
class Column:
    """One column of the file: its header and the field it is written from."""

    header: str
    name: str
    slot: Slot | None = None
    """What the field holds; `None` for the identity."""


@dataclass
class Layout:
    """The columns of a file: the record's own, then those of each field holding its children."""

    entity: type
    own: list[Column]
    children: dict[str, list[Column]] = field(default_factory=dict)

    @property
    def header(self) -> list[str]:
        """The first row of the file."""
        nested = [column.header for columns in self.children.values() for column in columns]
        return [column.header for column in self.own] + nested


def _columns(
    entity: type,
    specification: Specification | None,
    owner: type | None = None,
    prefix: str = "",
) -> tuple[list[Column], list[str]]:
    """The columns of `entity`'s own fields, the identity first, and the fields holding its
    children — none for a child: a flat file holds one level of them.

    1. A specification that names fields keeps those; the identity is always written.
    2. Left out: what a form cannot edit (the framework's fields, the computed ones), a to-one
       record (the file names it by its key field), and a child's key to `owner`.
    """
    model = model_of(entity)
    fixed = {one.field for one in model.computations if not one.editable} | set(
        model.computed
    )
    named = (
        set(specification.named)
        if specification is not None and specification.named
        else None
    )
    identity = identity_name(entity)
    columns = [Column(f"{prefix}{identity}", identity)]
    children: list[str] = []
    for name, slot in slots_of(entity).items():
        if (
            name in model.framework_fields
            or name in fixed
            or slot.holds == Holds.RECORD
            or (named is not None and name not in named)
            or (slot.holds == Holds.KEY and owner is not None and slot.target is owner)
        ):
            continue
        if slot.holds == Holds.RECORDS and not slot.by_id:
            if owner is None:
                children.append(name)
            continue
        columns.append(Column(f"{prefix}{name}", name, slot))
    return columns, children


def layout_of(entity: type, specification: Specification | None = None) -> Layout:
    """The columns an export of `entity` writes — those `specification` names when it names
    some, a children field named whole bringing all of its own.

        layout_of(Partner).header                 ->  ["id", "name", "vat", "country_id", …]
        layout_of(Partner, Specification({"name": Criteria()})).header   ->  ["id", "name"]
    """
    own, held = _columns(entity, specification)
    nodes = specification.root if specification is not None else {}
    children: dict[str, list[Column]] = {}
    for name in held:
        target = slots_of(entity)[name].target
        if target is None:
            continue
        node = nodes.get(name)
        children[name] = _columns(
            target, node.specification if node is not None else None, entity, f"{name}/"
        )[0]
    return Layout(entity, own, children)


def _named_by_id(columns: Sequence[Column]) -> dict[str, Criteria]:
    """A node for each many-to-many among `columns`: the records it holds are read with the
    page, so each one's key is written."""
    return {
        column.name: Criteria()
        for column in columns
        if column.slot is not None and column.slot.holds == Holds.RECORDS
    }


def loading(layout: Layout, criteria: Criteria | None = None) -> Criteria:
    """`criteria`, also bringing every relation the file writes: the children and the records
    of each many-to-many, the children's included. A node the caller named with its own
    specification is kept as it is; one named bare gets the many-to-manys its children hold.
    """
    asked = criteria or Criteria()
    named = dict(asked.specification.root) if asked.specification is not None else {}
    wanted = _named_by_id(layout.own)
    for name, columns in layout.children.items():
        inner = _named_by_id(columns)
        wanted[name] = Criteria(specification=Specification(inner)) if inner else Criteria()
    for name, node in wanted.items():
        held = named.get(name)
        if held is None:
            named[name] = node
        elif held.specification is None and node.specification is not None:
            named[name] = held.model_copy(update={"specification": node.specification})
    if not named:
        return asked
    return asked.model_copy(update={"specification": Specification(named)})


def _wanted(
    columns: Sequence[Column],
    record: Any,
    names: Mapping[type, Mapping[str, str]],
    wanted: dict[type, set[str]],
) -> None:
    """The identities `record` names through a key column and nobody has looked up yet."""
    for column in columns:
        slot = column.slot
        if slot is None or slot.target is None or slot.holds not in (Holds.KEY, Holds.KEYS):
            continue
        value = getattr(record, column.name)
        held = value if slot.holds == Holds.KEYS else [value]
        known = names.get(slot.target, {})
        unknown = {str(one) for one in held or () if one is not None} - set(known)
        if unknown:
            wanted.setdefault(slot.target, set()).update(unknown)


def find_names(
    repository: Any,
    layout: Layout,
    records: Sequence[Any],
    names: dict[type, dict[str, str]],
) -> None:
    """Each record the page names by a key, found once per export — one search per entity for
    the identities not found before — and kept in `names` as the natural key it is written as:
    `names[Country]["0192…"] = "BO"`. An entity without a natural key is named by its identity,
    with nothing to search."""
    wanted: dict[type, set[str]] = {}
    for record in records:
        _wanted(layout.own, record, names, wanted)
        for name, columns in layout.children.items():
            for child in getattr(record, name) or ():
                _wanted(columns, child, names, wanted)
    for kind, identities in wanted.items():
        natural, identity = key_of(kind), identity_name(kind)
        known = names.setdefault(kind, {})
        if natural == identity:
            known.update({one: one for one in identities})
            continue
        criteria = Criteria(
            selection=Condition(
                field=identity, operator=Operator.IN, value=sorted(identities)
            ),
            pagination=Pagination(limit=len(identities)),
            count=CountMode.NONE,
        )
        for found in repository.fetch_all(kind, criteria):
            key = getattr(found, natural)
            known[str(identity_of(found))] = "" if key is None else str(key)


def written(value: Any) -> Any:
    """A field's value as one cell holds it: text, a number, a boolean or a day as itself, a
    member of an enum as its value, anything else as its JSON."""
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, (str, bool, int, float, Decimal, date, time)):
        return value
    if isinstance(value, UUID):
        return str(value)
    return json.dumps(to_jsonable_python(value), ensure_ascii=False)


def _cell(column: Column, record: Any, names: Mapping[type, Mapping[str, str]]) -> Any:
    """What one column holds for `record`. A key whose record was not found is written as the
    identity itself, so importing it again says which one is missing."""
    value = getattr(record, column.name)
    slot = column.slot
    if slot is None:
        return str(value)
    if slot.target is None or slot.holds == Holds.VALUE:
        return written(value)
    known = names.get(slot.target, {})
    match slot.holds:
        case Holds.KEY:
            return None if value is None else known.get(str(value), str(value))
        case Holds.KEYS:
            keys = [known.get(str(one), str(one)) for one in value or ()]
            return ",".join(keys) or None
        case _:
            natural = key_of(slot.target)
            return ",".join(str(getattr(one, natural)) for one in value or ()) or None


def rows_of(
    layout: Layout, records: Sequence[Any], names: Mapping[type, Mapping[str, str]]
) -> Iterator[list[Any]]:
    """Each record as its rows: its own cells and its first children, then a row for each
    further child, the record's own cells blank — `None` for an empty cell."""
    for record in records:
        held = [list(getattr(record, name) or ()) for name in layout.children]
        for at in range(max([1, *(len(one) for one in held)])):
            if at == 0:
                row = [_cell(column, record, names) for column in layout.own]
            else:
                row = [None] * len(layout.own)
            for columns, children in zip(layout.children.values(), held, strict=True):
                if at < len(children):
                    row.extend(_cell(column, children[at], names) for column in columns)
                else:
                    row.extend([None] * len(columns))
            yield row
