"""What a form's values do to an aggregate in hand: `assign` puts them on it, read as each
field's type, and `recompute` runs the derivations they reach.

    changed = assign(invoice, {"discount": "50", "lines": [{"qty": 2, "price": "10.00"}]})
    recompute(invoice, changed)              subtotal, then total

Neither stores anything nor refuses a value because of a form hint: a hint is a client's
default, not a rule. A save runs `recompute_whole` itself, so the stored record and the preview
of it agree.
"""

import dataclasses
import inspect
from collections.abc import Iterable, Mapping
from functools import cache
from typing import Any, cast, get_args, get_origin

from pydantic import TypeAdapter, ValidationError

from sincpro_framework.ddd.entity.entity_collection import EntityCollection, identity_name
from sincpro_framework.ddd.entity.entity_meta import (
    Derivation,
    annotations_of,
    derivations_of,
    framework_fields,
    related_class,
    without_optional,
)
from sincpro_framework.ddd.exceptions import RelationNotResolved
from sincpro_framework.exceptions import ProgrammingError
from sincpro_framework.sincpro_logger import logger

UNREAD = object()
"""What a relation never read holds, for `assign` to compare with: anything is a change."""


@cache
def adapter_of(annotation: Any) -> TypeAdapter:
    """The one pydantic reader and writer of an annotation, built once."""
    return TypeAdapter(annotation)


def assign(record: Any, values: Mapping[str, Any]) -> tuple[str, ...]:
    """Put a form's values on `record`, each read as its field's type, and answer which fields
    now hold another value.

        in      Invoice(discount=0), {"discount": "50", "state": "draft"}
        out     ("discount",)                   discount is Decimal("50"); state was already draft

    1. A name that is not a field of the record is refused, naming it.
    2. A field the framework writes (`id`, `version`, `created_at`, a mixin's) is refused: a
       user's values never set an identity or the version a save is checked against.
    3. Each value is read through the field's annotation: `"50"` into a `Decimal`, `"card"`
       into its enum, a list of mappings into the line records.
       3.1 A mapping carrying an identity is the record the field already holds with it,
           edited in place, so its row is updated; an identity the field does not hold is
           refused, naming it.
       3.2 A mapping without one is a new record built through its own class, so a mapped
           one is a record its store can write.
       3.3 A value the type cannot read is refused, naming the field and why.
    4. A relation nobody read is replaced by a list without identities, exactly as assigning
       it directly does — blind: a save then keeps what reads it, and says so.
    Final: the fields whose value changed, in the order the values came.
    """
    owner = type(record)
    annotations = annotations_of(owner)
    unknown = [name for name in values if name not in annotations]
    if unknown:
        raise ProgrammingError(
            f"{owner.__name__} has no field {', '.join(unknown)} to assign"
        )
    written = sorted(set(values) & framework_fields(owner))
    if written:
        raise ProgrammingError(
            f"{owner.__name__}.{', '.join(written)} is written by the framework, not assigned"
        )
    changed: list[str] = []
    for name, value in values.items():
        read = _read(record, name, annotations[name], value)
        if _current(record, name) != read:
            changed.append(name)
        setattr(record, name, read)
    return tuple(changed)


def _current(record: Any, name: str) -> Any:
    """What the field holds now, never reading for it: a relation nobody read answers `UNREAD`,
    and assigning over it is a replacement, as `record.lines = [...]` is."""
    try:
        return getattr(record, name, None)
    except RelationNotResolved:
        return UNREAD


def _read(record: Any, name: str, annotation: Any, value: Any) -> Any:
    """`value` as the field `name` of `record` holds it.

    in      list[InvoiceLine], [{"qty": 2, "price": "10"}]   →  [InvoiceLine(qty=2, price=10)]
    in      list[InvoiceLine], [{"id": "l1", "qty": 5}]      →  [the held line l1, qty now 5]
    in      Decimal, "50"                                     →  Decimal("50")
    """
    owner = type(record)
    base = without_optional(annotation)
    members = get_args(base)
    if (
        get_origin(base) is list
        and members
        and _is_record_class(members[0])
        and isinstance(value, list)
    ):
        kind = members[0]
        held = _held_by_identity(record, name, kind, value)
        tied = _tied_to(record, name, kind)
        return [
            _edited_or_built(kind, {**one, **tied}, held) if isinstance(one, Mapping) else one
            for one in value
        ]
    if _is_record_class(base) and isinstance(value, Mapping):
        held = _held_by_identity(record, name, base, [value])
        return _edited_or_built(base, value, held)
    try:
        return adapter_of(annotation).validate_python(value)
    except ValidationError as error:
        raise ProgrammingError(
            f"{owner.__name__}.{name} cannot take {value!r}: {error.errors()[0]['msg']}"
        ) from error


def _tied_to(record: Any, name: str, kind: type) -> dict[str, Any]:
    """The key a record of `record.name` holds pointing back at `record`, filled in for it.

    in      Sale(id="s1"), "lines"     →  out  {"sale_id": "s1"}
    in      a relation a save does not write, a plain list    →  out  {}

    A form sends a line without the sale's key: it cannot know it, and for a sale previewed
    before it is stored the identity changes on every call. The relation knows the pair
    (`joined_by`, answered by the persistence adapter without reading anything), and the value
    is always the record's own — one sent by the form is replaced.
    """
    attribute = inspect.getattr_static(type(record), name, None)
    answers = getattr(attribute, "joined_by", None)
    pair = answers(record) if callable(answers) else None
    if not isinstance(pair, tuple) or len(pair) != 2:
        return {}
    mine, theirs = cast(tuple[str, str], pair)
    if theirs not in annotations_of(kind):
        return {}
    return {theirs: getattr(record, mine)}


def _held_by_identity(record: Any, name: str, kind: type, value: list[Any]) -> dict[str, Any]:
    """The records the field holds now, by identity — read only when a mapping names one.

    in      Order.lines holding l1 and l2, [{"id": "l1", "qty": 5}]   →  {"l1": line l1, "l2": …}
    in      the same, [{"qty": 5}]                                      →  {}   nothing to match
    """
    identity = _identity_of(kind)
    if not identity or not any(isinstance(one, Mapping) and identity in one for one in value):
        return {}
    try:
        current = getattr(record, name)
    except RelationNotResolved as error:
        raise ProgrammingError(
            f"{type(record).__name__}.{name} names records by {identity}, and they were not "
            "read: read them inside context(), or name them in the criteria's specification"
        ) from error
    held = (
        current
        if isinstance(current, (list, EntityCollection))
        else ([] if current is None else [current])
    )
    return {str(getattr(one, identity)): one for one in held}


def _identity_of(kind: type) -> str:
    """The identity a mapping names a held record by: the framework's own (`Entity.id`); a plain
    value record has none to match on."""
    identity = identity_name(kind)
    return identity if identity in framework_fields(kind) else ""


def _edited_or_built(kind: type, values: Mapping[str, Any], held: dict[str, Any]) -> Any:
    """The held record a mapping names by its identity, edited in place; or a new one."""
    identity = _identity_of(kind)
    if identity and identity in values:
        found = held.get(str(values[identity]))
        if found is None:
            raise ProgrammingError(
                f"{kind.__name__} {values[identity]} is not held here: an edited record is one "
                "the field already holds, and a new one comes without its identity"
            )
        assign(found, {name: one for name, one in values.items() if name != identity})
        return found
    return _built(kind, values)


def _built(kind: type, values: Mapping[str, Any]) -> Any:
    """A new nested record built through its own constructor, each value read as its field's
    type. Nothing the framework writes comes with it, its identity included."""
    annotations = annotations_of(kind)
    unknown = [name for name in values if name not in annotations]
    if unknown:
        raise ProgrammingError(f"{kind.__name__} has no field {', '.join(unknown)} to assign")
    written = sorted(set(values) & framework_fields(kind))
    if written:
        raise ProgrammingError(
            f"{kind.__name__}.{', '.join(written)} is written by the framework, not assigned"
        )
    read = {name: _plain(kind, name, annotations[name], one) for name, one in values.items()}
    try:
        return kind(**read)
    except TypeError as error:
        raise ProgrammingError(
            f"{kind.__name__} cannot be built from {dict(values)!r}: {error}"
        )


def _plain(kind: type, name: str, annotation: Any, value: Any) -> Any:
    """A value for a record being built: nothing is held yet, so every nested one is new."""
    base = without_optional(annotation)
    members = get_args(base)
    if get_origin(base) is list and members and _is_record_class(members[0]):
        if isinstance(value, list):
            return [
                _built(members[0], one) if isinstance(one, Mapping) else one for one in value
            ]
    if _is_record_class(base) and isinstance(value, Mapping):
        return _built(base, value)
    try:
        return adapter_of(annotation).validate_python(value)
    except ValidationError as error:
        raise ProgrammingError(
            f"{kind.__name__}.{name} cannot take {value!r}: {error.errors()[0]['msg']}"
        ) from error


def _is_record_class(annotation: Any) -> bool:
    return isinstance(annotation, type) and dataclasses.is_dataclass(annotation)


def recompute(record: Any, changed: Iterable[str] = ()) -> tuple[str, ...]:
    """Run, in order, every derivation the change reaches — the records inside it first — and
    answer the fields of `record` it changed.

        in      Invoice, ("discount",)          →  total runs; subtotal does not
        in      Invoice, ("lines",)             →  each line's amount, then subtotal, then total
        in      Invoice, ()                     →  every derivation

    1. A name in `changed` that is not a field is refused, naming it.
    2. The records it owns are computed first, whole (`owned`): a line's amount before the
       subtotal that sums it; a field whose records moved is reached.
    3. With `changed` empty every derivation of the record runs, in order.
    4. Otherwise a derivation runs when it reads a field the change reached; its own field is
       then reached too, so a derivation of a derivation follows.
    Final: the derived fields of `record` whose value moved.
    """
    owner = type(record)
    unknown = sorted(set(changed) - set(annotations_of(owner))) if changed else []
    if unknown:
        raise ProgrammingError(
            f"{owner.__name__} has no field {', '.join(unknown)} to recompute"
        )
    return _tree(record, set(changed), False, set())


def recompute_whole(record: Any) -> tuple[str, ...]:
    """Every derivation of the record, the records it owns first — what a save runs, so a total
    over its lines reads lines already computed.

    in      Sale(lines=[Line(qty=2, price=10, amount=0)], subtotal=0)
    out     Line.amount = 20, then Sale.subtotal = 20

    **A save never reads, and never computes over a relation it does not hold whole.** A
    derivation that reads a relation not loaded, cut by a specification, or assigned without a
    whole reading behind it is skipped and keeps its stored value; a derivation that reads it
    computes from that stored value. A relation never read cannot have changed, so that skip is
    silent; one held in part may have, so a warning names the field, the relation and how to
    read it whole. The records held are computed either way. An aggregate with no derivations
    is not looked into at all.
    """
    return _tree(record, set(), True, set())


def _tree(record: Any, reached: set[str], tolerant: bool, seen: set[int]) -> tuple[str, ...]:
    if id(record) in seen:
        return ()
    seen.add(id(record))
    for name in owned(type(record)):
        # The records held are computed, whether the relation is held whole or in part: each
        # line's own amount reads only the line. What reads the relation is decided in `_ran`.
        if not (_holds_whole(record, name) or _held_in_part(record, name)):
            continue
        value = getattr(record, name)
        moved = False
        for one in value if isinstance(value, (list, EntityCollection)) else (value,):
            if dataclasses.is_dataclass(one) and not isinstance(one, type):
                moved = bool(_tree(one, set(), tolerant, seen)) or moved
        if moved and reached:
            reached.add(name)
    derivations = derivations_of(type(record))
    if not derivations:
        return ()
    return _ran(record, derivations, reached, tolerant)


@cache
def owned(declared: type) -> tuple[str, ...]:
    """The fields holding records a computation of `declared` has to compute first: those its
    derivations read, and the lists of records that declare derivations of their own. Never
    another aggregate it only points at.

    in      Order: subtotal ← (lines,);  OrderLine: amount ← (qty, price)    →  ("lines",)
    in      Box: no derivations;  Item: none                                  →  ()
    """
    derivations = derivations_of(declared)
    read = {name for one in derivations for name in one.depends}
    found: list[str] = []
    for name, annotation in annotations_of(declared).items():
        kind, many = related_class(annotation)
        if kind is None or not _is_record_class(kind):
            continue
        if name in read or (many and derivations_of(kind)):
            found.append(name)
    return tuple(found)


def _holds_whole(record: Any, name: str) -> bool:
    """Whether `record` holds the field `name` whole, answered without reading anything.

    A plain field is held. A relation an adapter manages answers for itself (`holds_whole`):
    not loaded, cut by a specification or assigned blind is not held whole.
    """
    attribute = inspect.getattr_static(type(record), name, None)
    answers = getattr(attribute, "holds_whole", None)
    return bool(answers(record)) if callable(answers) else True


def _held_in_part(record: Any, name: str) -> str:
    """How `record` holds part of the relation `name` — `"cut"`, `"blind"` — or `""`, answered
    without reading anything. A plain field, and a relation never read, hold no part."""
    attribute = inspect.getattr_static(type(record), name, None)
    answers = getattr(attribute, "held_in_part", None)
    return str(answers(record)) if callable(answers) else ""


def _warn_kept(record: Any, derivation: Derivation) -> None:
    """A derivation a save skipped over a relation held in part keeps its stored value, which
    may no longer agree with the records it sums: said once, with the way out."""
    owner = type(record).__name__
    for name in derivation.depends:
        how = _held_in_part(record, name)
        if how:
            logger.warning(
                f"{owner}.{derivation.field} is not recomputed on save: {owner}.{name} is held "
                f"{how}, so the stored value is kept and may not match it. Read {name} whole "
                "— inside context(), or with a specification that does not filter or page it — "
                "for the save to compute it"
            )
            return


def _ran(
    record: Any, derivations: tuple[Derivation, ...], reached: set[str], tolerant: bool
) -> tuple[str, ...]:
    """Runs the derivations `reached` asks for (all of them when it is empty). Tolerant, one that
    reads a field the record does not hold whole is skipped, and its field keeps what it holds.
    """
    every = not reached
    moved: list[str] = []
    for derivation in derivations:
        if not every and reached.isdisjoint(derivation.depends):
            continue
        if tolerant and not all(_holds_whole(record, one) for one in derivation.depends):
            _warn_kept(record, derivation)
            continue
        try:
            value = _computed(derivation, record)
            before = getattr(record, derivation.field, None)
        except RelationNotResolved:
            if not tolerant:
                raise
            continue
        setattr(record, derivation.field, value)
        reached.add(derivation.field)
        if value != before:
            moved.append(derivation.field)
    return tuple(moved)


def _computed(derivation: Derivation, record: Any) -> Any:
    """The derivation's value for `record`. A method named in the declaration is called as the
    record's own, so a subclass that overrides it is heard; any other callable is called as is.
    """
    by = derivation.by
    name = getattr(by, "__name__", "")
    if inspect.isfunction(by) and name != "<lambda>":
        own = getattr(type(record), name, None)
        if inspect.isfunction(own):
            return own(record)
    return by(record)
