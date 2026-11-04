"""What a form's values do to an aggregate in hand: `apply_values` puts them on it, read as each
field's type, and `recompute` runs the computations they reach.

    changed = apply_values(invoice, {"discount": "50", "lines": [{"qty": 2, "price": "10.00"}]})
    recompute(invoice, changed)              subtotal, then total

Neither stores anything nor refuses a value because of a form hint: a hint is a client's
default, not a rule. A save runs `recompute_whole` itself, so the stored record and the preview
of it agree.
"""

import dataclasses
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextvars import ContextVar
from functools import cache
from typing import Any, get_args, get_origin

from pydantic import TypeAdapter, ValidationError

from sincpro_framework.context import ContextKey, use_context
from sincpro_framework.ddd import exceptions
from sincpro_framework.ddd.editing.preview import (
    is_read_only,
    located,
    warn_form_at,
    warnings_at,
)
from sincpro_framework.ddd.entity.entity_collection import EntityCollection, identity_name
from sincpro_framework.ddd.entity.entity_meta import (
    Check,
    Computation,
    annotations_of,
    checks_of,
    computations_of,
    framework_fields,
    related_class,
    without_optional,
)
from sincpro_framework.ddd.entity.relations import relation_attribute
from sincpro_framework.sincpro_logger import logger

_SKIPPED: ContextVar[tuple[Any, ...] | None] = ContextVar(
    "sincpro_checks_skipped", default=None
)
"""The last (execution, entity, reason) whose skipped checks were logged — said once, not per row."""

UNREAD = object()
"""What a relation never read holds, for `apply_values` to compare with: anything is a change."""


@cache
def adapter_of(annotation: Any) -> TypeAdapter:
    """The one pydantic reader and writer of an annotation, built once."""
    return TypeAdapter(annotation)


def _current(record: Any, name: str) -> Any:
    """What the field holds now, never reading for it: a relation nobody read answers `UNREAD`,
    and assigning over it is a replacement, as `record.lines = [...]` is."""
    try:
        return getattr(record, name, None)
    except exceptions.RelationNotResolved:
        return UNREAD


def apply_values(
    record: Any, values: Mapping[str, Any], adopt: bool = False
) -> tuple[str, ...]:
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
           refused, naming it — or, with `adopt`, a new record built under that identity: what
           a draft holds of a record it made, read back the same each time.
       3.2 A mapping without one is a new record built through its own class, so a mapped
           one is a record its store can write.
       3.3 A value the type cannot read is refused, naming the field and why.
    4. A relation nobody read is replaced by a list without identities, exactly as assigning
       it directly does — blind: a save then leaves what reads it at its stored value, and
       says so.
    Final: the fields whose value changed, in the order the values came.

    Each refusal of the values is an `InvalidValue` — a `ClientError`: they came from whoever
    sent the form, and every wire answers it as a request to fix — carrying where the value is
    in the form, a line's cell included (`lines.3.quantity` → `#/values/lines/3/quantity`). Relations the code never read are the code's
    (`ProgrammingError`).
    """
    owner = type(record)
    annotations = annotations_of(owner)
    unknown = [name for name in values if name not in annotations]
    if unknown:
        raise exceptions.InvalidValue(
            f"{owner.__name__} has no field {', '.join(unknown)} to assign", (unknown[0],)
        )
    written = sorted(set(values) & framework_fields(owner))
    if written:
        raise exceptions.InvalidValue(
            f"{owner.__name__}.{', '.join(written)} is written by the framework, not assigned",
            (written[0],),
        )
    changed: list[str] = []
    for name, value in values.items():
        read = _read(record, name, annotations[name], value, adopt)
        if _current(record, name) != read:
            changed.append(name)
        setattr(record, name, read)
    return tuple(changed)


def _identity_of(kind: type) -> str:
    """The identity a mapping names a held record by: the framework's own (`Entity.id`); a plain
    value record has none to match on."""
    identity = identity_name(kind)
    return identity if identity in framework_fields(kind) else ""


def _is_record_class(annotation: Any) -> bool:
    """Whether `annotation` is a record class — a dataclass, an entity or a value record — so
    a mapping for it is built or edited field by field instead of validated as one value."""
    return isinstance(annotation, type) and dataclasses.is_dataclass(annotation)


def form_values(
    kind: type, values: Mapping[str, Any], nested: bool = False
) -> dict[str, Any]:
    """A record's values as a form sends them back to `apply_values`: what the framework writes left
    out at every level — a nested record retaining its identity, the way a form names the line
    it edits.

        form_values(Sale, {"id": "s1", "version": 2, "discount": "5",
                           "lines": [{"id": "l1", "version": 1, "qty": 2}]})
            ->  {"discount": "5", "lines": [{"id": "l1", "qty": 2}]}

    1. What to leave out: every field the framework writes — at a nested level, all but the
       record's own identity.
    2. Each value, in order, its field's optional stripped:
       2.1 a field left out is skipped;
       2.2 a list of records — each mapping in it read the same way, nested; anything else
           in the list left as it is;
       2.3 one record held as a mapping — read the same way, nested;
       2.4 anything else left as it is.
    Final: the values a form edits, ready for `apply_values`.
    """
    annotations = annotations_of(kind)
    own_identity = _identity_of(kind) if nested else ""
    left_out = framework_fields(kind) - {own_identity}
    written: dict[str, Any] = {}
    for name, value in values.items():
        if name in left_out:
            continue
        base = without_optional(annotations.get(name))
        members = get_args(base)
        if get_origin(base) is list and members and _is_record_class(members[0]):
            if isinstance(value, list):
                value = [
                    form_values(members[0], one, True) if isinstance(one, Mapping) else one
                    for one in value
                ]
        elif _is_record_class(base) and isinstance(value, Mapping):
            value = form_values(base, value, True)
        written[name] = value
    return written


def _tied_to(record: Any, name: str, kind: type) -> dict[str, Any]:
    """The key a record of `record.name` holds pointing back at `record`, filled in for it.

    in      Sale(id="s1"), "lines"     →  out  {"sale_id": "s1"}
    in      a relation a save does not write, a plain list    →  out  {}

    A form sends a line without the sale's key: it cannot know it, and for a sale previewed
    before it is stored the identity changes on every call. The relation knows the pair
    (`joined_by`, answered by the persistence adapter without reading anything), and the value
    is always the record's own — one sent by the form is replaced.
    """
    attribute = relation_attribute(type(record), name)
    pair = attribute.joined_by(record) if attribute is not None else None
    if pair is None:
        return {}
    mine, theirs = pair
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
    except exceptions.RelationNotResolved as error:
        raise exceptions.ProgrammingError(
            f"{type(record).__name__}.{name} names records by {identity}, and they were not "
            "read: read them inside context(), or name them in the criteria's specification"
        ) from error
    held = (
        current
        if isinstance(current, (list, EntityCollection))
        else ([] if current is None else [current])
    )
    return {str(getattr(one, identity)): one for one in held}


def _records(
    owner: type,
    name: str,
    kind: type,
    value: list[Any],
    made: Callable[[Mapping[str, Any]], Any],
) -> list[Any]:
    """Each item of a list of records as the field holds it, in order.

        in      [{"qty": 2}, <InvoiceLine l1>]    →  [InvoiceLine(qty=2), <InvoiceLine l1>]
        in      ["l1"]                            →  InvalidValue pointing at lines/0

    1. A mapping is a record to make — built, or a held one edited (`made`).
    2. A record of the line's class is taken as it is.
    3. Anything else — an id, a number — is refused: a list of records holds records. An id
       of another aggregate is read as its record before `apply_values` (`EntityReads.referenced`)
       only where the store names it by id; here it would be held as a bare value.
    Final: the records; a refusal inside one points at its position (`within`).
    """
    records: list[Any] = []
    for index, one in enumerate(value):
        try:
            if isinstance(one, Mapping):
                records.append(made(one))
            elif isinstance(one, kind):
                records.append(one)
            else:
                raise exceptions.InvalidValue(
                    f"{owner.__name__}.{name} holds {kind.__name__} records, given as a "
                    f"mapping or as the record; it was given {one!r}",
                    (),
                )
        except exceptions.InvalidValue as error:
            raise error.within(name, index) from error
    return records


def _read(record: Any, name: str, annotation: Any, value: Any, adopt: bool) -> Any:
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
        return _records(
            owner,
            name,
            kind,
            value,
            lambda one: _edited_or_built(kind, {**one, **tied}, held, adopt),
        )
    if _is_record_class(base) and isinstance(value, Mapping):
        held = _held_by_identity(record, name, base, [value])
        try:
            return _edited_or_built(base, value, held, adopt)
        except exceptions.InvalidValue as error:
            raise error.within(name) from error
    try:
        return adapter_of(annotation).validate_python(value)
    except ValidationError as error:
        raise exceptions.InvalidValue(
            f"{owner.__name__}.{name} cannot take {value!r}: {error.errors()[0]['msg']}",
            (name,),
        ) from error


def _built(kind: type, values: Mapping[str, Any], adopt: bool) -> Any:
    """A new nested record built through its own constructor, each value read as its field's
    type. Nothing the framework writes comes with it — its identity included, unless adopting:
    then the record takes the identity it names."""
    identity = _identity_of(kind)
    adopted = values.get(identity) if adopt and identity else None
    values = {name: one for name, one in values.items() if not (adopted and name == identity)}
    annotations = annotations_of(kind)
    unknown = [name for name in values if name not in annotations]
    if unknown:
        raise exceptions.InvalidValue(
            f"{kind.__name__} has no field {', '.join(unknown)} to assign", (unknown[0],)
        )
    written = sorted(set(values) & framework_fields(kind))
    if written:
        raise exceptions.InvalidValue(
            f"{kind.__name__}.{', '.join(written)} is written by the framework, not assigned",
            (written[0],),
        )
    read = {
        name: _plain(kind, name, annotations[name], one, adopt)
        for name, one in values.items()
    }
    try:
        built = kind(**read)
    except TypeError as error:
        raise exceptions.InvalidValue(
            f"{kind.__name__} cannot be built from {dict(values)!r}: {error}", ()
        ) from error
    if adopted:
        setattr(built, identity, adopted)
    return built


def _edited_or_built(
    kind: type, values: Mapping[str, Any], held: dict[str, Any], adopt: bool
) -> Any:
    """The held record a mapping names by its identity, edited in place; or a new one — under
    the identity it names, when adopting."""
    identity = _identity_of(kind)
    if identity and identity in values:
        found = held.get(str(values[identity]))
        if found is None and adopt:
            return _built(kind, values, adopt)
        if found is None:
            raise exceptions.InvalidValue(
                f"{kind.__name__} {values[identity]} is not held here: an edited record is one "
                "the field already holds, and a new one comes without its identity",
                (identity,),
            )
        apply_values(
            found, {name: one for name, one in values.items() if name != identity}, adopt
        )
        return found
    return _built(kind, values, adopt)


def _plain(kind: type, name: str, annotation: Any, value: Any, adopt: bool) -> Any:
    """A value for a record being built: nothing is held yet, so every nested one is new."""
    base = without_optional(annotation)
    members = get_args(base)
    if get_origin(base) is list and members and _is_record_class(members[0]):
        if isinstance(value, list):
            kind = members[0]
            return _records(kind, name, kind, value, lambda one: _built(kind, one, adopt))
    if _is_record_class(base) and isinstance(value, Mapping):
        try:
            return _built(base, value, adopt)
        except exceptions.InvalidValue as error:
            raise error.within(name) from error
    try:
        return adapter_of(annotation).validate_python(value)
    except ValidationError as error:
        raise exceptions.InvalidValue(
            f"{kind.__name__}.{name} cannot take {value!r}: {error.errors()[0]['msg']}",
            (name,),
        ) from error


@cache
def owned(declared: type) -> tuple[str, ...]:
    """The fields holding records a pass over `declared` has to run first: those its
    computations read or its checks watch, and the lists of records that declare computations or
    checks of their own. Never another aggregate it only points at.

        owned(Order)    ->  ("lines",)    # Order: subtotal ← lines; OrderLine: amount ← qty
        owned(Sale)     ->  ("lines",)    # nothing reads lines, but SaleLine derives amount
        owned(Box)      ->  ()            # Box and Item declare no computations and no checks

    Context: a single record field counts only when a computation reads it or a check watches
    it; a list counts also when its records compute or check something themselves. Cached per
    class.
    """
    read = {name for one in computations_of(declared) for name in one.depends} | {
        name for one in checks_of(declared) for name in one.watches
    }
    found: list[str] = []
    for name, annotation in annotations_of(declared).items():
        kind, many = related_class(annotation)
        if kind is None or not _is_record_class(kind):
            continue
        if name in read or (many and (computations_of(kind) or checks_of(kind))):
            found.append(name)
    return tuple(found)


def _holds_whole(record: Any, name: str) -> bool:
    """Whether `record` holds the field `name` whole, answered without reading anything.

    A plain field is held. A relation an adapter manages answers for itself (`holds_whole`):
    not loaded, cut by a specification or assigned blind is not held whole.
    """
    attribute = relation_attribute(type(record), name)
    return attribute.holds_whole(record) if attribute is not None else True


def _held_in_part(record: Any, name: str) -> str:
    """How `record` holds part of the relation `name` — `"cut"`, `"blind"` — or `""`, answered
    without reading anything. A plain field, and a relation never read, hold no part."""
    attribute = relation_attribute(type(record), name)
    return attribute.held_in_part(record) if attribute is not None else ""


def _warn_stale_computation(record: Any, computation: Computation) -> None:
    """A computation a save skipped over a relation held in part retains its stored value,
    which may no longer agree with the records it sums: said once, with the way out."""
    owner = type(record).__name__
    for name in computation.depends:
        how = _held_in_part(record, name)
        if how:
            logger.warning(
                f"{owner}.{computation.field} is not recomputed on save: {owner}.{name} is held "
                f"{how}, so the stored value stays and may not match it. Read {name} whole "
                "— inside context(), or with a specification that does not filter or page it — "
                "for the save to compute it"
            )
            return


@dataclasses.dataclass
class _Pass:
    """One run over an aggregate and the records it owns.

    `saving` is a save: what reads a relation the record does not hold whole is skipped, no
    editable computation runs, and a failed check is collected in `failures` to refuse.
    Otherwise a failed check is warned to the form — and checks run at all only when `checking`:
    inside `read_only()`, where a warning has somewhere to go.
    """

    saving: bool
    checking: bool
    failures: list[exceptions.InvalidValue] = dataclasses.field(default_factory=list)
    seen: set[int] = dataclasses.field(default_factory=set)


def _split(owner: type, changed: Sequence[str]) -> tuple[set[str], dict[str, list[str]]]:
    """The fields of `owner` a change names, and what it names inside the records each holds.

        _split(Invoice, ["discount", "lines.2.qty", "lines.3"])
            ->  ({"discount"}, {"lines": ["2.qty", "3"]})

    A name that is not a field of `owner` is refused, naming it: the change came from a form.
    """
    named: set[str] = set()
    inside: dict[str, list[str]] = {}
    for one in changed:
        head, dot, rest = one.partition(".")
        if dot:
            inside.setdefault(head, []).append(rest)
        else:
            named.add(head)
    unknown = sorted((named | set(inside)) - set(annotations_of(owner))) if changed else []
    if unknown:
        raise exceptions.ClientError(
            f"{owner.__name__} has no field {', '.join(unknown)} to recompute"
        )
    return named, inside


def _by_position(owner: type, name: str, paths: list[str]) -> dict[int, list[str]]:
    """What a change names inside each record of the list `name`, by its position: `"2.qty"`
    is `qty` of the third, `"2"` the third whole (`""`).

        _by_position(Invoice, "lines", ["2.qty", "2.price", "3"])
            ->  {2: ["qty", "price"], 3: [""]}

    A path that does not start with a position is refused, showing the form it takes.
    """
    found: dict[int, list[str]] = {}
    for path in paths:
        position, _, rest = path.partition(".")
        if not position.isdigit():
            raise exceptions.ClientError(
                f"{owner.__name__}.{name}.{path} names no record of {name}: one is named by its "
                f"position in the list, as in {name}.2.qty"
            )
        found.setdefault(int(position), []).append(rest)
    return found


def _ran(
    record: Any,
    computations: tuple[Computation, ...],
    reached: set[str],
    every: bool,
    run: _Pass,
) -> tuple[str, ...]:
    """Runs, in order, the computations of `record` the pass asks for, and answers the fields
    whose value moved.

    1. A computation runs when `every` asks for all of them or a change reached a field it
       reads; an editable one only when a change reached it, and never in a save — it proposes
       a value, and the one typed over it is the one stored.
    2. In a save, one that reads a field the record does not hold whole is skipped and its field
       retains what it holds (`_warn_stale_computation`); one that reads a relation nobody asked
       for is skipped too. Elsewhere that is the code's mistake, raised.
    3. A field computed because a change reached it is reached in turn, so what reads it follows.
    Final: the fields whose value moved.
    """
    moved: list[str] = []
    for computation in computations:
        reaches = not reached.isdisjoint(computation.depends)
        if computation.editable:
            if run.saving or not reaches:
                continue
        elif not (every or reaches):
            continue
        if run.saving and not all(_holds_whole(record, one) for one in computation.depends):
            _warn_stale_computation(record, computation)
            continue
        try:
            value = getattr(record, computation.method)()
        except exceptions.RelationNotResolved:
            if not run.saving:
                raise
            continue
        before = getattr(record, computation.field, None)
        setattr(record, computation.field, value)
        if reaches:
            reached.add(computation.field)
        if value != before:
            moved.append(computation.field)
    return tuple(moved)


def _checked(
    record: Any, checks: tuple[Check, ...], reached: set[str], whole: bool, run: _Pass
) -> None:
    """Runs the checks of `record` the pass asks for: each failure collected to refuse, in a
    save, or warned to the form.

    1. A check runs when `whole` asks for all of them or a change reached a field it watches.
    2. In a save, one that watches a field the record does not hold whole is skipped: a rule
       over lines nobody read whole cannot be judged. Lines held in part may break it unseen, so
       that skip is said; a relation never read cannot have changed, so that one is silent.
    3. A failure with no path points at the first field the check watches; where the record
       sits in the aggregate goes in front (`located`), so a line's failure reaches its cell.
    """
    owner = type(record).__name__
    for one in checks:
        if not (whole or not reached.isdisjoint(one.watches)):
            continue
        if run.saving:
            partial = [name for name in one.watches if not _holds_whole(record, name)]
            if partial:
                held = [name for name in partial if _held_in_part(record, name)]
                if held:
                    logger.warning(
                        f"{owner}.{one.method} is not checked on save: {owner}.{held[0]} is held "
                        f"{_held_in_part(record, held[0])}, so the rule cannot be judged. Read "
                        f"{held[0]} whole — inside context(), or with a specification that does "
                        "not filter or page it — for the save to check it"
                    )
                continue
        try:
            getattr(record, one.method)()
        except exceptions.RelationNotResolved:
            if not run.saving:
                raise
        except exceptions.InvalidValue as error:
            path = located(error.path or one.watches[:1])
            if run.saving:
                run.failures.append(exceptions.InvalidValue(str(error), path, error.field))
            else:
                warn_form_at(str(error), path)


def _tree(
    record: Any,
    changed: Sequence[str],
    every: bool,
    whole: bool,
    judged: bool,
    run: _Pass,
) -> tuple[str, ...]:
    """The computations and checks of `record` and of the records it owns, innermost first —
    what both `recompute` and `recompute_whole` run.

    1. A record already visited answers nothing: a cycle of references ends here.
    2. What the change names: fields of the record, and paths inside the records it holds
       (`lines.2.qty`); each of those fields is reached.
    3. Each owned field (`owned`) the record holds whole or in part: every record in it runs its
       own tree — every computation; the checks a path names inside it, or all of them when the
       pass checks everything or the change named the field whole — its warnings and failures
       pointing at where it sits (`("lines", 2)`). Records a save of `record` does not write —
       another aggregate it points at (`joined_by` is `None`) — are computed but not judged:
       their rules are their own save's.
       3.1 When one of them moved and the change named fields, the owned field is reached, so
           what sums it runs.
    4. Its computations run (`_ran`), then, when the pass checks and `record` is `judged`, its
       checks (`_checked`).
    Final: the computed fields of `record` that moved.
    """
    if id(record) in run.seen:
        return ()
    run.seen.add(id(record))
    owner = type(record)
    named, inside = _split(owner, changed)
    reached = named | set(inside)
    for name in owned(owner):
        # The records held are computed, whether the relation is held whole or in part: each
        # line's own amount reads only the line. What reads the relation is decided in `_ran`.
        if not (_holds_whole(record, name) or _held_in_part(record, name)):
            continue
        value = getattr(record, name)
        many = isinstance(value, (list, EntityCollection))
        paths = inside.get(name, [])
        positions = _by_position(owner, name, paths) if many else {0: paths}
        attribute = relation_attribute(owner, name)
        written = attribute is None or attribute.joined_by(record) is not None
        moved = False
        for index, one in enumerate(value if many else (value,)):
            if dataclasses.is_dataclass(one) and not isinstance(one, type):
                within = positions.get(index, [])
                with warnings_at((name, index) if many else (name,)):
                    moved = (
                        bool(
                            _tree(
                                one,
                                [path for path in within if path],
                                True,
                                whole or name in named or "" in within,
                                judged and written,
                                run,
                            )
                        )
                        or moved
                    )
        if moved and not every:
            reached.add(name)
    moved_here = _ran(record, computations_of(owner), reached, every, run)
    if run.checking and judged:
        _checked(record, checks_of(owner), reached, whole, run)
    return moved_here


def recompute(record: Any, changed: Iterable[str] = ()) -> tuple[str, ...]:
    """Run, in order, every computation the change reaches — the records inside it first — and
    answer the fields of `record` it changed; inside `read_only()`, warn the form of every check
    the change reaches.

        in      Invoice, ("discount",)          →  total runs; subtotal does not
        in      Invoice, ("lines",)             →  each line's amount, then subtotal, then total
        in      Invoice, ("lines.2.product",)   →  the third line's editable price, then the rest
        in      Invoice, ()                     →  every computation, no check

    1. A name in `changed` that is not a field is refused, naming it; a path names a field of a
       record the aggregate owns, by its position (`lines.2.qty`).
    2. The records it owns are computed first, whole (`owned`): a line's amount before the
       subtotal that sums it; a field whose records moved is reached.
    3. With `changed` empty every computation of the record runs, in order, but no editable one
       and no check: nothing was changed yet.
    4. Otherwise a computation runs when it reads a field the change reached; its own field is
       then reached too, so a computation of a computation follows. An editable one runs only so.
    5. Inside `read_only()`, each check whose fields the change reached warns the form when it
       fails (`FormWarning`); outside it no check runs — the save checks them all.
    Final: the computed fields of `record` whose value moved.
    """
    asked = list(changed)
    return _tree(
        record, asked, not asked, False, True, _Pass(saving=False, checking=is_read_only())
    )


def _skipping_checks(record: Any) -> bool:
    """Whether this call's saves skip the checks (`ContextKey.SKIP_CHECKS`): said once per
    execution and entity with the reason and who asked, so a skipped rule is never silent —
    outside an execution, once per thread for the same reason.

        # bus.context(skip_checks="legacy import 2026-11"), user ana, 10 000 saves
        ->  True for each; one warning: "Sale saved without its checks: legacy import
            2026-11 (asked by ana)"
    """
    context = use_context()
    reason = context.get(ContextKey.SKIP_CHECKS)
    if not reason:
        return False
    said = (context.get(ContextKey.EXECUTION_ID), type(record).__name__, reason)
    if _SKIPPED.get() != said:
        _SKIPPED.set(said)
        logger.warning(
            f"{type(record).__name__} saved without its checks: {reason} "
            f"(asked by {context.get(ContextKey.USER_ID) or 'nobody named'})"
        )
    return True


def recompute_whole(record: Any) -> tuple[str, ...]:
    """Every computation of the record, the records it owns first, then every check — what a save
    runs, so a total over its lines reads lines already computed and a broken rule is refused.

    in      Sale(lines=[Line(qty=2, price=10, amount=0)], subtotal=0)
    out     Line.amount = 20, then Sale.subtotal = 20

    **A save never reads, and never computes or checks over a relation it does not hold whole.**
    A computation that reads a relation not loaded, cut by a specification, or assigned without
    a whole reading behind it is skipped and retains its stored value; a computation that reads
    it computes from that stored value; a check that watches it is not judged. A relation never
    read cannot have changed, so that skip is silent; one held in part may have, so a warning
    names the field, the relation and how to read it whole. The records held are computed either
    way. An editable computation does not run: what the user typed is stored. An aggregate with
    no computations and no checks is not looked into at all.

    Raises `ChecksFailed` with every failure — the lines' first — when any check failed, unless
    the call skips the checks (`ContextKey.SKIP_CHECKS`, logged).
    """
    run = _Pass(saving=True, checking=not _skipping_checks(record))
    moved = _tree(record, (), True, True, True, run)
    if run.failures:
        raise exceptions.ChecksFailed(tuple(run.failures))
    return moved
