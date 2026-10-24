"""What a mapped model is, and everything only it can answer about a filter.

Derived by the adapter's `describe`, never declared. It reaches a client as `entity_meta_data`
so a filter form can be built without anybody maintaining a schema.

The rules about what may be asked live here rather than in whoever is asking: a field knows
which operators it takes and how to read a value written as text, and the model knows which of
its fields can be ordered or grouped by. See `docs/persistence/reference.md`.

**Nothing here inspects a mapper.** This module says what a definition *is* and what it
accepts; reading one off a mapped class is the adapter's job, because that is the half that
knows the ORM.
"""

import dataclasses
import inspect
import sys
import types
import uuid
from datetime import UTC, date, datetime, time, tzinfo
from decimal import Decimal
from enum import Enum, StrEnum
from functools import cache
from typing import (
    Any,
    ClassVar,
    Literal,
    Union,
    cast,
    get_args,
    get_origin,
    get_type_hints,
)
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, PydanticSchemaGenerationError, TypeAdapter, computed_field
from pydantic_core import PydanticSerializationError

from sincpro_framework.context.entrypoint.facade import use_context
from sincpro_framework.ddd import exceptions
from sincpro_framework.ddd.criteria import (
    MULTI_VALUED,
    RESOLVERS,
    All,
    Any_,
    Condition,
    Criteria,
    Expression,
    FromField,
    Not,
    Operator,
    ResolutionContext,
    ResolverMeta,
    Sort,
    Specification,
    conditions_of,
    has_dynamic_values,
    holds_text,
    mapped,
    resolve,
)
from sincpro_framework.ddd.entity.computed import computations_of
from sincpro_framework.ddd.entity.derivations import Derivations, Derive
from sincpro_framework.ddd.entity.entity import Translated
from sincpro_framework.ddd.entity.entity_collection import Dropped
from sincpro_framework.ddd.entity.presentation import (
    Fields,
    Presentation,
    When,
    field_name,
)
from sincpro_framework.sincpro_abstractions import DataTransferObject

TRUTHY = frozenset({"1", "true", "yes", "on", "t"})


class FieldType(StrEnum):
    """The logical type of a field — what a client renders and which operators it offers — as
    opposed to the Python type it was declared with.

        describe(Dataset).fields["row_count"].type   ->  FieldType.INTEGER   ("integer")
        describe(Dataset).fields["runs"].type        ->  FieldType.ONE2MANY

    Context: a small, closed vocabulary that travels as text in the definition, so a client
    in any language branches on it. Odoo's words for the three relations, because they
    already say the cardinality and the side of the key. `UNKNOWN` is what a type nobody
    mapped reads as: it is offered no comparison, so it is shown but not filtered by value.
    """

    TEXT = "text"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"
    TEXT_LIST = "text[]"
    TRANSLATED = "translated"
    EMBEDDED = "embedded"
    """A value object stored inside the row, as JSON: it has a shape, `definition`, and no
    identity or life of its own."""
    MANY2ONE = "many2one"
    ONE2MANY = "one2many"
    MANY2MANY = "many2many"
    UUID = "uuid"
    UNKNOWN = "unknown"

    @property
    def is_relational(self) -> bool:
        """Whether a field of this type points at another aggregate, one with its own life,
        that has to be brought: the three Odoo words say the cardinality and the side of the key.

        >>> FieldType.MANY2ONE.is_relational, FieldType.EMBEDDED.is_relational
        (True, False)
        """
        return self in (FieldType.MANY2ONE, FieldType.ONE2MANY, FieldType.MANY2MANY)

    def read(self, raw: Any, timezone: tzinfo = UTC) -> Any:
        """Reads a value written as text into what this type holds.

            FieldType.INTEGER.read("1000")     ->  1000      # the text a URL carries
            FieldType.DATETIME.read("2026-03-14T15:09:26")
                ->  datetime(2026, 3, 14, 15, 9, 26)
            FieldType.DATETIME.read(date(2026, 3, 14), timezone=ZoneInfo("America/La_Paz"))
                ->  datetime(2026, 3, 14, 4, 0, tzinfo=UTC)    # La Paz's midnight
            FieldType.BOOLEAN.read("yes")      ->  True
            FieldType.INTEGER.read("banana")
                ->  Unreadable: invalid literal for int() with base 10: 'banana'

        Anything not text is already read and comes back untouched. A text that will not
        convert raises `Unreadable`, which the model turns into a dropped condition.

        1. A date asked as a DATETIME: its midnight in `timezone`, as a UTC instant.
        2. Anything else that is not text: as it is.
        3. Text, by type:
           3.1 INTEGER, NUMBER — `int`, `float`;
           3.2 BOOLEAN — one of the truthy words, any case; any other word is `False`;
           3.3 DATETIME, DATE — ISO text; a datetime keeps the zone the text wrote, if any;
           3.4 UUID — a `uuid.UUID`;
           3.5 any other type — the text as it is.
        Final: a `ValueError` on the way is `Unreadable`, saying why.
        """
        if (
            self is FieldType.DATETIME
            and isinstance(raw, date)
            and not isinstance(raw, datetime)
        ):
            return datetime.combine(raw, time.min, tzinfo=timezone).astimezone(UTC)
        if not isinstance(raw, str):
            return raw
        try:
            match self:
                case FieldType.INTEGER:
                    return int(raw)
                case FieldType.NUMBER:
                    return float(raw)
                case FieldType.BOOLEAN:
                    return raw.lower() in TRUTHY
                case FieldType.DATETIME:
                    return datetime.fromisoformat(raw)
                case FieldType.DATE:
                    return date.fromisoformat(raw)
                case FieldType.UUID:
                    return uuid.UUID(raw)
                case _:
                    return raw
        except ValueError as error:
            raise exceptions.Unreadable(str(error)) from error


COMPARABLE: tuple[Operator, ...] = (
    Operator.EQ,
    Operator.NE,
    Operator.GT,
    Operator.GTE,
    Operator.LT,
    Operator.LTE,
    Operator.BETWEEN,
)

SAME_ROW: tuple[Operator, ...] = tuple(
    one for one in COMPARABLE if one is not Operator.BETWEEN
)
"""The operators that compare a column with another column of the same row."""

AGGREGATES_BY_TYPE: dict[FieldType, tuple[str, ...]] = {
    FieldType.INTEGER: (
        "sum",
        "avg",
        "min",
        "max",
        "count",
        "count_distinct",
        "percentile",
        "median",
    ),
    FieldType.NUMBER: (
        "sum",
        "avg",
        "min",
        "max",
        "count",
        "count_distinct",
        "percentile",
        "median",
    ),
    FieldType.DATE: ("min", "max", "count", "count_distinct"),
    FieldType.DATETIME: ("min", "max", "count", "count_distinct"),
    FieldType.TEXT: ("min", "max", "count", "count_distinct"),
    FieldType.UUID: ("min", "max", "count", "count_distinct"),
    FieldType.BOOLEAN: ("bool_and", "bool_or", "count"),
}
"""What a field of each type can be folded with. A type that is not here is not folded."""

OPERATORS_BY_TYPE: dict[FieldType, tuple[Operator, ...]] = {
    FieldType.TEXT: (
        Operator.EQ,
        Operator.NE,
        Operator.LIKE,
        Operator.STARTS_WITH,
        Operator.IN,
        Operator.NOT_IN,
    ),
    FieldType.INTEGER: COMPARABLE + (Operator.IN, Operator.NOT_IN),
    FieldType.NUMBER: COMPARABLE,
    FieldType.BOOLEAN: (Operator.EQ,),
    FieldType.DATE: COMPARABLE,
    FieldType.DATETIME: COMPARABLE,
    FieldType.TEXT_LIST: (
        Operator.CONTAINS,
        Operator.NOT_CONTAINS,
        Operator.EQ,
        Operator.NE,
    ),
    FieldType.TRANSLATED: (Operator.LIKE,),
    FieldType.UUID: (Operator.EQ, Operator.NE, Operator.IN, Operator.NOT_IN),
    FieldType.EMBEDDED: (),
    FieldType.MANY2ONE: (),
    FieldType.ONE2MANY: (),
    FieldType.MANY2MANY: (),
    FieldType.UNKNOWN: (),
}

IS_NULL = Operator.IS_NULL

LOGICAL_TYPES: dict[Any, FieldType] = {
    str: FieldType.TEXT,
    int: FieldType.INTEGER,
    float: FieldType.NUMBER,
    Decimal: FieldType.NUMBER,
    bool: FieldType.BOOLEAN,
    datetime: FieldType.DATETIME,
    date: FieldType.DATE,
    uuid.UUID: FieldType.UUID,
}


def without_optional(annotation: Any) -> Any:
    """An annotation without its `| None`.

        in  str | None  →  out  str
        in  list[str]   →  out  list[str]

    Whether a value may be absent is the column's answer, not the annotation's.
    """
    if get_origin(annotation) in (Union, types.UnionType):
        real = [arg for arg in get_args(annotation) if arg is not type(None)]
        if len(real) == 1:
            return real[0]
    return annotation


def annotations_of(declared: type) -> dict[str, Any]:
    """The class's field annotations with forward references resolved against its module.

        in  Dataset  →  out  {'dataset_id': str, 'row_count': int, …}

    A `ClassVar` is left out: it belongs to the class, not to a record (`Entity.presentation`).
    A reference that does not resolve fails here, at boot, and names the class: a typo in a
    forward reference should not surface as a missing relation at the first request.
    """
    try:
        hints = get_type_hints(declared, vars(sys.modules[declared.__module__]))
    except NameError as error:
        raise exceptions.ProgrammingError(
            f"{declared.__name__} names a type that cannot be resolved at runtime: {error}"
        ) from error
    return {
        name: hint
        for name, hint in hints.items()
        if hint is not ClassVar and get_origin(hint) is not ClassVar
    }


def enum_of(annotation: Any) -> type[Enum] | None:
    """The enum an annotation names, if it names one.

    in  Stage | None   →  out  Stage
    in  str            →  out  None
    """
    base = without_optional(annotation)
    return base if isinstance(base, type) and issubclass(base, Enum) else None


def members_of(annotation: Any) -> list[Any]:
    """The values an enum field may hold, in declaration order; empty when it is not one."""
    members = enum_of(annotation)
    return [] if members is None else [member.value for member in members]


def type_translations(annotation: Any) -> Translated:
    """What an enum calls itself — its `translations()`, the shape an `Entity` answers its own
    name with. Empty for any other annotation, and for an enum that declares none.

    in  Priority | None   Priority.translations() → {"default": "Priority", "es": "Prioridad"}
    out {"default": "Priority", "es": "Prioridad"}
    """
    translator = getattr(enum_of(annotation), "translations", None)
    return cast(Translated, translator()) if callable(translator) else {}


def choice_translations_of(annotation: Any) -> dict[str, Translated]:
    """What each value of an enum is called — its `choice_translations()` — keyed by the value
    as text, as JSON keys travel, in the order the enum declares its values. Empty when the
    annotation is not an enum or the enum names none; a value it leaves out has no label.

        # Priority.choice_translations() -> {Priority.HIGH: {"default": "High", "es": "Alta"}}
        choice_translations_of(Priority | None)
            ->  {"high": {"default": "High", "es": "Alta"}}
        choice_translations_of(str)   ->  {}
        # choice_translations() naming "urgent", not a Priority value
            ->  raises ProgrammingError("Priority.choice_translations() names 'urgent', …")

    A key that is not one of the enum's values is a typo, refused when the model is described.
    A key may be the member or its value: each is read back as the member first.
    """
    members = enum_of(annotation)
    translator = getattr(members, "choice_translations", None)
    if members is None or not callable(translator):
        return {}
    by_member: dict[Any, Translated] = {}
    for value, translated in cast(dict[Any, Translated], translator()).items():
        try:
            by_member[members(value)] = translated
        except ValueError as error:
            raise exceptions.ProgrammingError(
                f"{members.__name__}.choice_translations() names {value!r}, which is not one "
                f"of its values: {', '.join(repr(one.value) for one in members)}"
            ) from error
    return {str(one.value): by_member[one] for one in members if one in by_member}


@cache
def _labels_of_types(declared: type) -> dict[str, Translated]:
    """`{field_name: translated}` for every field whose type names itself — an enum with
    `translations()`. Once per class: the annotations are resolved here, and a change event
    asks for the labels at every write."""
    return {
        name: named
        for name, annotation in annotations_of(declared).items()
        if (named := type_translations(annotation))
    }


def field_translations(declared: type, key: str) -> dict[str, Translated]:
    """`{field_name: translated}` for every field of `declared` that carries
    `field(metadata={key: translated})` — empty for a class that is not a dataclass, or that
    declared none. `key` is `"label"` or `"help"`; read once per class, alongside the rest of
    the definition, never per instance.

    A field that declares no `label` is called what its type calls itself: a category named once,
    in its enum, is named the same in every aggregate that uses it. The field's own `label` wins.

        # class Ticket: title = field(metadata={"label": {"default": "Title"}});
        #               priority: Priority — whose translations() name it "Priority"
        field_translations(Ticket, "label")
            ->  {"priority": {"default": "Priority", "es": "Prioridad"},
                 "title": {"default": "Title"}}
        field_translations(Ticket, "help")   ->  {}    # no field declared one
        field_translations(dict, "label")    ->  {}    # not a dataclass

    Only `label` falls back on the type's name; `help` is what the fields declared, alone.
    """
    if not dataclasses.is_dataclass(declared):
        return {}
    declared_here = {
        one.name: cast(Translated, one.metadata[key])
        for one in dataclasses.fields(declared)
        if key in one.metadata
    }
    if key != "label":
        return declared_here
    return {**_labels_of_types(declared), **declared_here}


def related_class(annotation: Any) -> tuple[type | None, bool]:
    """The class an annotation points at, and whether it names several of them.

        related_class(list[Run])         ->  (Run, True)
        related_class(Shelf | None)      ->  (Shelf, False)
        related_class(str)               ->  (str, False)     # a plain type is still a class
        related_class(dict[str, str])    ->  (None, False)
        related_class(set[Run])          ->  (None, False)

    Context: only a `list` names several; a set or a tuple of records is not a relation here.
    """
    base = without_optional(annotation)
    many = get_origin(base) is list
    if many:
        members = get_args(base)
        base = members[0] if members else None
    return (base if isinstance(base, type) else None), many


def logical_type(annotation: Any) -> FieldType:
    """What a Python annotation means as a column type.

        in  int              →  out  FieldType.INTEGER
        in  list[str]        →  out  FieldType.TEXT_LIST
        in  dict[str, str]   →  out  FieldType.TRANSLATED  a text in several languages
        in  SomeOddClass     →  out  FieldType.UNKNOWN     nothing is offered over it

    Read from the annotation and not the column, because `column_names` is `Text` on disk and
    `list[str]` in the model — and only the second admits `contains`.

    1. The optional stripped: `int | None` is read as `int`.
    2. A list: `TEXT_LIST` when it holds `str`, else `UNKNOWN`.
    3. `dict[str, str]`: `TRANSLATED`.
    4. An enum: as the primitive it is — a `StrEnum` text, an `IntEnum` integer — and
       `UNKNOWN` when it is neither.
    Final: the primitive's type by `LOGICAL_TYPES` — `UNKNOWN` for any other class.
    """
    base = without_optional(annotation)
    if get_origin(base) is list:
        members = get_args(base)
        return FieldType.TEXT_LIST if members and members[0] is str else FieldType.UNKNOWN
    if get_origin(base) is dict and get_args(base) == (str, str):
        # A text in several languages, `{"default": …, "es": …}`, stored through
        # `TranslatedText`: searched in every language at once, never ordered.
        return FieldType.TRANSLATED
    if isinstance(base, type) and issubclass(base, Enum):
        # An enum is filtered as what its members are — a `StrEnum` as text, an `IntEnum`
        # as integers — and its members become the field's `choices`.
        for primitive in (str, int):
            if issubclass(base, primitive):
                return LOGICAL_TYPES[primitive]
        return FieldType.UNKNOWN
    return LOGICAL_TYPES.get(base, FieldType.UNKNOWN)


class FieldMeta(DataTransferObject):
    """The definition of one field as a client needs it: its type, what it may be asked, how a
    form shows it and, for a relation or a value object, what is on the other side.

        describe(Dataset).fields["row_count"]
        ->  FieldMeta(type=INTEGER, nullable=False, sortable=True, ops=(=, !=, >, …))

    Derived, never declared — built by `for_column`, `for_relation` or `for_embedded` from
    what the reader knows. It also reads a condition's value as its type (`read`), which is
    why the rules of a field live with its definition and not with whoever asks.
    """

    type: FieldType
    nullable: bool

    choices: list[Any] = []
    """The values an enum field may hold — what a select is built from."""
    choice_labels: dict[str, Translated] = {}
    """What each of `choices` is called, keyed by the value as text — the enum's
    `choice_translations()`. Empty when the enum names none; a value missing here has no label."""

    exact: bool = False
    """A decimal: a condition's value is read as a `Decimal` and compared exactly, so `"0.1"`
    finds `Decimal("0.10")` — a float would not."""

    label: Translated = {}
    """This field's own name, for a screen — `field(metadata={"label": {"default": "Title"}})`
    on the dataclass. Empty when the field declared none."""
    help: Translated = {}
    """What this field is for, longer than the label — `field(metadata={"help": {...}})`.
    Empty when the field declared none."""

    readonly: bool = False
    """A form shows it read-only. A hint: nothing refuses a write to it."""
    required: bool = False
    """A form asks for it. A hint: nothing refuses a save without it."""
    default: Any = None
    """What a form for a new record starts with: the dataclass default, as JSON. `None` when
    the field has none, or builds it each time (`default_factory`)."""
    readonly_when: Expression | None = None
    """While this holds over the record, a form shows the field read-only. A hint."""
    required_when: Expression | None = None
    """While this holds over the record, a form asks for the field. A hint."""
    visible_when: Expression | None = None
    """A form shows the field only while this holds over the record. A hint."""

    sortable: bool
    """False for every nullable column: a keyset comparison omits rows holding NULL, so
    ordering by one loses rows from the pagination in silence."""

    groupable: bool = False
    """Whether a grouping may split by this field (`Level(field=…)`): a scalar that is not a list
    or a translation. A date is grouped by a grain or by a part, which the level says."""
    aggregates: tuple[str, ...] = ()
    """The functions this field can be folded with (`Measure(function=…, field=…)`), by its type.
    A client offers exactly these and never carries the table."""
    computed: bool = False
    """The field is computed (`computed(sql=…)`), not a column: it is read-only and is filtered,
    ordered and grouped like one."""
    owner: str | None = None
    """The extension that adds this field, by class name (`MoveDelivery`); `None` for a field the
    entity has itself. An extension's field may be absent on a record that has no extension, so
    it is nullable and not sortable here whatever its column says."""

    ops: tuple[Operator, ...]
    """Included although derivable from `type`, so a consumer never has to carry the table."""
    many: bool = False
    """Several of them: a `one2many`, a `many2many`, or a list of embedded values."""
    relation: str | None = None
    """For a relational field, the aggregate on the other side, by name: `Run` for
    `Dataset.runs`. What Odoo's `fields_get` calls `relation`."""
    identified_by: str | None = None
    """For a relational field, the column that identifies the relation: this aggregate's own
    column for a `many2one` (`Run.dataset` by `Run.dataset_id`), the related aggregate's for a
    `one2many` (`Dataset.runs` by `Run.dataset_id`), Odoo's `relation_field`. A client filters
    by it without expanding anything. `None` when nothing said how the relation resolves."""
    parent_field: str | None = None
    """The field read on THIS side to match the relation, with `related_field` the one read on
    the other. The pair says outright what `identified_by` leaves to a convention that depends
    on the cardinality, and a relation matched on two fields that are neither an identity can
    only be read here."""
    related_field: str | None = None
    """The field read on the OTHER side — see `parent_field`.

    A relation's own scope is deliberately absent from all of this, and not by omission: the
    scope is not a filter laid over the relation, it is part of what the relation means.
    `Invoice.lines` is this invoice's lines as the mapper defines them, and that a voided one
    is not among them is a fact of the model rather than a caveat to disclose."""
    definition: "Meta | None" = None
    """The shape of the other side: always for an embedded value, and for a relational field
    once it was expanded, cut by the same mask."""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def kind(self) -> Literal["scalar", "embedded", "relation"]:
        """The coarse answer to «what is this name», before the finer `type`: a value in the
        row, a value object inside the row, or another aggregate to bring. Travels with the
        definition so a client branches on one word.

            describe(Work).fields["author"].kind      ->  "relation"    # MANY2ONE
            describe(Work).fields["size"].kind        ->  "embedded"
            describe(Work).fields["title"].kind       ->  "scalar"      # TEXT

        Every type that is neither relational nor embedded is a scalar.
        """
        if self.type.is_relational:
            return "relation"
        if self.type is FieldType.EMBEDDED:
            return "embedded"
        return "scalar"

    @classmethod
    def for_relation(
        cls,
        logical: FieldType,
        relation: str,
        identified_by: str | None,
        nullable: bool = False,
        parent_field: str | None = None,
        related_field: str | None = None,
    ) -> "FieldMeta":
        """A field that points at another aggregate. Nothing to filter or order by directly:
        the key it hangs on is a scalar field of its own, and that one takes the operators."""
        return cls(
            type=logical,
            nullable=nullable,
            sortable=False,
            ops=(),
            many=logical is not FieldType.MANY2ONE,
            relation=relation,
            identified_by=identified_by,
            parent_field=parent_field,
            related_field=related_field,
        )

    @classmethod
    def for_embedded(cls, definition: "Meta", many: bool, nullable: bool) -> "FieldMeta":
        """A value object inside the row: its shape travels, nothing about it is queried."""
        return cls(
            type=FieldType.EMBEDDED,
            nullable=nullable,
            sortable=False,
            ops=(),
            many=many,
            definition=definition,
        )

    @classmethod
    def for_column(
        cls,
        logical: FieldType,
        nullable: bool,
        members: list[Any] | None = None,
        exact: bool = False,
        choice_labels: dict[str, Translated] | None = None,
    ) -> "FieldMeta":
        """The definition of one column, from what the reader knows about it.

            in      INTEGER, nullable=False   →  sortable, every comparable operator
            in      TEXT, nullable=True       →  not sortable, and `is null` offered too

        1. Offer the operators its type takes, plus `is_null` when it may be absent.
        2. List the values of an enum as its choices, with what each is called.
        3. Final: sortable only when NOT NULL and not a list or a translation — a keyset
           comparison drops rows holding NULL, and there is no order over a JSON value a
           cursor could page through.
        """
        unsortable = (FieldType.TEXT_LIST, FieldType.TRANSLATED)
        return cls(
            type=logical,
            nullable=nullable,
            choices=list(members or []),
            choice_labels=dict(choice_labels or {}),
            exact=exact,
            sortable=not nullable and logical not in unsortable,
            groupable=logical in AGGREGATES_BY_TYPE,
            aggregates=AGGREGATES_BY_TYPE.get(logical, ()),
            ops=OPERATORS_BY_TYPE[logical] + ((IS_NULL,) if nullable else ()),
        )

    def accepts(self, operator: Operator) -> bool:
        """Whether this field takes that question.

        >>> describe(Dataset).field("row_count").accepts(Operator.GT)
        True
        >>> describe(Dataset).field("row_count").accepts(Operator.LIKE)
        False
        """
        return operator in self.ops

    def _value(self, raw: Any, timezone: tzinfo = UTC) -> Any:
        """One raw value read as this field holds it.

            exact field, "0.10"   ->  Decimal("0.10")      never through a float
            INTEGER, "1000"       ->  1000

        1. A decimal field reads text and numbers (not booleans) as a `Decimal` from their
           text, so `0.1` stays `0.1` — a failure is `Unreadable`.
        2. Final: anything else is read by the type (`FieldType.read`).
        """
        if self.exact and isinstance(raw, (str, int, float)) and not isinstance(raw, bool):
            try:
                return Decimal(str(raw))
            except ArithmeticError as error:
                raise exceptions.Unreadable(f"{raw!r} is not a decimal") from error
        return self.type.read(raw, timezone)

    def read(self, condition: Condition, timezone: tzinfo = UTC) -> Condition:
        """Context: the same condition with its value in the type this column compares against.

            row_count.read(Condition(field="row_count", value="1000", operator=Operator.GT))
                ->  Condition(field='row_count', value=1000, operator=<Operator.GT: '>'>)
            row_count.read(Condition(field="row_count", value="5", operator=Operator.IN))
                ->  Condition(field='row_count', value=[5], operator=<Operator.IN: 'in'>)
            row_count.read(Condition(..., value="1000", operator=Operator.BETWEEN))
                ->  Unreadable: 'between' compares against two bounds; got '1000'

        1. `is_null` is the exception: its value is the question asked, not the field's type.
        2. `between` reads both of its two bounds — anything but a pair is `Unreadable`.
        3. A multi-valued operator reads every member; one value alone is a list of one.
        4. A list of texts: `eq` and `ne` take the list as it came; `contains` asks about one
           member, so its value is a member's type.
        Final: any other condition has its value read as the field's type (`_value`).
        """
        if condition.operator is Operator.IS_NULL:
            return condition.model_copy(
                update={"value": FieldType.BOOLEAN.read(condition.value)}
            )

        if condition.operator is Operator.BETWEEN:
            given = condition.value
            if not isinstance(given, list) or len(given) != 2:
                raise exceptions.Unreadable(
                    f"'between' compares against two bounds; got {condition.value!r}"
                )
            return condition.model_copy(
                update={"value": [self._value(one, timezone) for one in given]}
            )

        if condition.operator in MULTI_VALUED:
            given = (
                condition.value if isinstance(condition.value, list) else [condition.value]
            )
            return condition.model_copy(
                update={"value": [self._value(one, timezone) for one in given]}
            )

        if self.type is FieldType.TEXT_LIST:
            # `contains` asks about one member, so its value is a member's type; `eq` and `ne`
            # ask about the whole list and take it as it came.
            if condition.operator in (Operator.EQ, Operator.NE):
                return condition
            return condition.model_copy(
                update={"value": FieldType.TEXT.read(condition.value)}
            )

        return condition.model_copy(update={"value": self._value(condition.value, timezone)})


def ambient_resolution() -> ResolutionContext:
    """What a dynamic value reads when nobody gave it a resolution: this moment, and the server's
    own context — its `timezone` (an IANA name, UTC without one) and `week_starts_on`.

        # inside bus.context({"timezone": "America/La_Paz", "week_starts_on": 6})
        ambient_resolution()
            ->  ResolutionContext(now=<now, UTC>, timezone=ZoneInfo("America/La_Paz"),
                                  week_starts_on=6, context=<the context>)
        # {"timezone": "La Paz"}  ->  raises ProgrammingError("the context's timezone ...")

    A timezone that is not an IANA name is the server's wiring mistake, not the caller's.
    """
    context = use_context()
    zone = context.get("timezone", "UTC")
    try:
        timezone = ZoneInfo(zone)
    except ZoneInfoNotFoundError as error:
        raise exceptions.ProgrammingError(
            f"the context's timezone {zone!r} is not a timezone name"
        ) from error
    return ResolutionContext(
        now=datetime.now(UTC),
        timezone=timezone,
        week_starts_on=context.get("week_starts_on", 0),
        context=context,
    )


class Meta(DataTransferObject):
    """What a caller is told about an aggregate: one map of fields, scalar, embedded and
    relational alike, each saying with `type` which it is. The shape of Odoo's `fields_get`.
    """

    aggregate: str
    identity: str
    default_order: str
    """The list order when a criteria names none: the entity's `DEFAULT_ORDER`, or newest
    identity first for a class that answers none."""
    get_id: str = ""
    """The field `Get` and `GetMany` find a record by (`DEFAULT_GET_ID`); the identity unless the
    entity names a natural key. Empty for a value object."""
    display: str = ""
    """The field shown beside the identity: what a select lists and a reference carries
    (`DEFAULT_DISPLAY`). Empty when the aggregate names none."""
    search: Criteria | None = None
    """How a typed text finds a record (`DEFAULT_LITERAL_SEARCH`): a template whose `where`
    holds `TEXT`, which a client fills as `matching` does. `None` when nothing is searched."""
    detail: Criteria | None = None
    """What one record brings when nobody asks otherwise (`DEFAULT_READING`). `detail_of`
    answers it; `None` is every scalar and no relation."""
    fields: dict[str, FieldMeta]
    name: Translated
    """The aggregate's own name, exactly as the class's `translations()` answered it. A
    field's own label/help live on `fields[name].label`/`.help`, not here."""
    resolvers: list[ResolverMeta] = Field(default_factory=lambda: list(RESOLVERS))
    """The dynamic values a condition's value may be (`today`, `context`, …): what a filter builder
    offers beside a literal."""

    @property
    def relations(self) -> dict[str, FieldMeta]:
        """The relational fields alone, a view over `fields`: what may be expanded.

        >>> set(describe(Dataset).relations)
        {'runs', 'producer'}
        """
        return {
            name: field for name, field in self.fields.items() if field.type.is_relational
        }

    def _kept(
        self, condition: Condition, timezone: tzinfo = UTC
    ) -> tuple[Condition | None, Dropped | None]:
        """One condition, kept and read — or dropped with the reason.

            in      Condition(row_count, "1000", GT)
            out     (Condition(row_count, 1000, GT), None)          it fits
            or      (None, Dropped(field='gone', reason='unknown_field'))

        Three things can be wrong and they are not the same: an unknown field, an operator the
        type does not take, and a value that will not read. A column compared with another
        (`FromField`) needs the other to exist, to be of the same type, and a comparing operator.
        """
        field = self.fields.get(condition.field)
        if field is None:
            return None, Dropped(field=condition.field, reason="unknown_field")

        if not field.accepts(condition.operator):
            return None, Dropped(field=condition.field, reason="unsupported_operator")

        if isinstance(condition.value, FromField):
            other = self.fields.get(condition.value.name)
            if other is None:
                return None, Dropped(field=condition.value.name, reason="unknown_field")
            if condition.operator not in SAME_ROW or other.type is not field.type:
                return None, Dropped(field=condition.field, reason="bad_value")
            return condition, None

        try:
            return field.read(condition, timezone), None
        except exceptions.Unreadable:
            return None, Dropped(field=condition.field, reason="bad_value")

    def field(self, name: str) -> FieldMeta:
        """The field, or a refusal naming it. For the callers that cannot go on without one.

        >>> describe(Dataset).field("row_count").type
        <FieldType.INTEGER: 'integer'>
        >>> describe(Dataset).field("nope")
        InvalidCriteria: 'nope': no such field on dataset
        """
        found = self.fields.get(name)
        if found is None:
            raise exceptions.InvalidCriteria(f"'{name}': no such field on {self.aggregate}")
        return found

    def orderable(self, name: str) -> FieldMeta:
        """The field, if a page may be ordered by it.

            in  "registered_at"  →  out  FieldMeta(...)    NOT NULL, so it can be paged
            in  "produced_by"    →  out  InvalidCriteria   nullable
            in  "nope"           →  out  InvalidCriteria   no such field

        A nullable column is refused because a page ordered by one silently drops the rows
        that hold no value — the one failure here that loses data without raising.
        """
        found = self.field(name)
        if not found.sortable:
            raise exceptions.InvalidCriteria(
                f"cannot order by '{name}': it is nullable, and a page ordered by a nullable "
                "column silently drops the rows that hold no value"
            )
        return found

    def accept_specification(
        self, specification: Specification | None
    ) -> tuple[Specification | None, list[Dropped]]:
        """The part of a specification this model can answer, and what it could not.

            in      {"name": {}, "legacy_flag": {}}
            out     ({"name": {}}, [Dropped(legacy_flag, 'unknown_field')])

            in      {"legacy_flag": {}}       nothing survives
            out     ({}, [ … ])               the identity alone, NOT everything

            in      nothing asked
            out     left alone: the whole record

        A field is kept when the model has it, as a column or as a relation, and dropped and
        named when it does not. Which of the two it is never reaches the caller: telling them
        apart is this object's job, which is why a specification has one bucket and not two.
        """
        if specification is None:
            return None, []

        dropped: list[Dropped] = []
        kept = {}
        for name, criteria in specification.root.items():
            if name in self.fields:
                kept[name] = criteria
            else:
                dropped.append(Dropped(field=name, reason="unknown_field"))
        return Specification(kept), dropped

    def masked(self, specification: Specification) -> list[str]:
        """The names a specification keeps besides the identity.

            in      {"code": {}, "name": {}}    →  out  ["code", "name"]
            in      {}                          →  out  ["name"]      the display, when named

        An empty specification is a reference: the identity and the display travel together.
        """
        named = specification.named
        if not named and self.display:
            return [self.display]
        return named

    def only(self, specification: Specification | None) -> "Meta":
        """This definition as the masked payload will look: the same shape, narrowed.

            in      {"name": {}}        →  out  fields={identity, name}
            in      {}                  →  out  fields={identity, display}
            in      nothing asked       →  out  every scalar and embedded field, no relation
            in      {"size": {"specification": {"width": {}}}}
                                        →  out  the embedded shape cut to {width}, identity kept

        **This is what makes the mask one mask.** A client that asked for three fields gets
        three in the payload AND three in the definition, so its filter builder and its column
        menu cannot offer what it was not given.

        The identity always stays: a record whose id did not travel cannot be opened,
        refreshed or cached, so masking it away makes the rest useless.

        1. Nothing asked: every field but the relations — a relation is brought only when
           asked for by name.
        2. Else the names, the identity first and each once: the identity, then what the
           specification keeps (`masked` — the display when it names none).
           2.1 A name this definition does not have is skipped.
           2.2 An embedded field whose node asks for a specification of its own has its
               definition cut by it, in turn.
        Final: a copy of this definition holding only those fields, in that order.
        """
        if specification is None:
            return self.model_copy(
                update={
                    "fields": {
                        name: field
                        for name, field in self.fields.items()
                        if not field.type.is_relational
                    }
                }
            )

        kept: dict[str, FieldMeta] = {}
        for name in dict.fromkeys([self.identity, *self.masked(specification)]):
            field = self.fields.get(name)
            if field is None:
                continue
            node = specification.root.get(name)
            if (
                field.type is FieldType.EMBEDDED
                and field.definition is not None
                and node is not None
                and node.specification is not None
            ):
                field = field.model_copy(
                    update={"definition": field.definition.only(node.specification)}
                )
            kept[name] = field
        return self.model_copy(update={"fields": kept})

    def accept(
        self, expression: Expression | None, resolution: ResolutionContext | None = None
    ) -> tuple[Expression | None, list[Dropped]]:
        """The part of a filter this model can answer, and the list of what it could not.

        Nothing raises for a condition it cannot use: it is dropped and reported, because a shared
        link outlives the schema it was written against and a dropped filter always widens.

        A dynamic value (`today`, the user) is read first, so everything downstream — the store,
        the SQL, the key of a cache — sees only what actually ran. Without a `resolution` it is
        built from this moment and the framework's own context.

            meta.accept(Condition(field="row_count", operator=Operator.GT, value="1000"))
                ->  (Condition(field='row_count', operator=<Operator.GT>, value=1000), [])
            meta.accept(Condition(field="gone", value="1"))
                ->  (None, [Dropped(field='gone', reason='unknown_field')])
            meta.accept(All([row_count > "1000", row_count > "lots"]))
                ->  (Condition(row_count > 1000),
                     [Dropped(field='row_count', reason='bad_value')])

        1. Dynamic values resolved — by `resolution`, or the ambient one when none is given.
        2. No resolution yet, and a plain date compared with a DATETIME field: the ambient
           one is taken anyway, so the date's midnight is read in the context's timezone.
        3. The timezone: the resolution's, else UTC.
        4. Each leaf through `_kept`:
           4.1 an unknown field — `unknown_field`; an operator its type does not take —
               `unsupported_operator`;
           4.2 another column (`FromField`): it must exist, be of the same type and be
               compared by a same-row operator — else `bad_value`;
           4.3 else its value read as the field's type — a value that will not read is
               `bad_value`.
        Final: the tree of what was kept, a group that lost every part gone, and the drops.
        """
        if has_dynamic_values(expression):
            resolution = resolution or ambient_resolution()
            expression = resolve(expression, resolution)

        if resolution is None and any(
            isinstance(leaf.value, date)
            and not isinstance(leaf.value, datetime)
            and (held := self.fields.get(leaf.field)) is not None
            and held.type is FieldType.DATETIME
            for leaf in conditions_of(expression)
        ):
            resolution = ambient_resolution()
        timezone = resolution.timezone if resolution is not None else UTC

        dropped: list[Dropped] = []

        def kept(condition: Condition) -> Condition | None:
            """One leaf through `_kept`: read, or `None` with its loss noted in `dropped`."""
            read, loss = self._kept(condition, timezone)
            if loss is not None:
                dropped.append(loss)
            return read

        return mapped(expression, kept), dropped


def _worded(fields: dict[str, "FieldMeta"], declared: type) -> dict[str, "FieldMeta"]:
    """`fields`, each carrying the label/help its own dataclass field declared — a class that
    declared none gets the defaults `FieldMeta` already has."""
    labels, helps = field_translations(declared, "label"), field_translations(
        declared, "help"
    )
    if not labels and not helps:
        return fields
    return {
        name: meta.model_copy(
            update={"label": labels.get(name, {}), "help": helps.get(name, {})}
        )
        for name, meta in fields.items()
    }


TEXT_TYPES = frozenset({FieldType.TEXT, FieldType.TRANSLATED})


@dataclasses.dataclass(frozen=True)
class Presented:
    """What an entity answers about how it is read and shown, checked against the class: its
    `DEFAULT_*` class methods, and the form hints of its `presentation` with field names in
    place of lambdas.

        in      Account(DEFAULT_GET_ID=code, DEFAULT_ORDER=code, DEFAULT_DISPLAY=name)
        out     Presented(get_id="code", display="name", search=name like TEXT,
                          order=(Sort(code),), detail=None, …)
    """

    get_id: str = ""
    display: str = ""
    search: Criteria | None = None
    order: tuple[Sort, ...] = ()
    detail: Criteria | None = None
    readonly: frozenset[str] = frozenset()
    required: frozenset[str] = frozenset()
    readonly_when: dict[str, Expression] = dataclasses.field(default_factory=dict)
    required_when: dict[str, Expression] = dataclasses.field(default_factory=dict)
    visible_when: dict[str, Expression] = dataclasses.field(default_factory=dict)


def _entries(answered: object) -> tuple[object, ...]:
    """What a declaration answered, as entries: a list, a tuple or one value alone.

    in      [a.code, a.name]    →  out  (a.code, a.name)
    in      a.code              →  out  (a.code,)
    """
    return tuple(answered) if isinstance(answered, (list, tuple)) else (answered,)


KEY_TYPES = frozenset({FieldType.TEXT, FieldType.INTEGER, FieldType.UUID})
"""What a field `Get` finds a record by may hold: a value a person or a URL names it with."""


def _get_id_checked(owner: str, answered: object, annotations: dict[str, Any]) -> str:
    """`DEFAULT_GET_ID`: a text, integer or uuid field of the entity's own."""
    if not isinstance(answered, str) or answered not in annotations:
        raise exceptions.ProgrammingError(
            f"{owner}.DEFAULT_GET_ID answers {answered!r}, which is not a field of {owner}"
        )
    kind = logical_type(annotations[answered])
    if kind not in KEY_TYPES:
        raise exceptions.ProgrammingError(
            f"{owner}.DEFAULT_GET_ID answers {answered}, a {kind} field: a record is found by "
            f"a text, integer or uuid field"
        )
    return answered


def _display_checked(owner: str, answered: object, annotations: dict[str, Any]) -> str:
    """`DEFAULT_DISPLAY`: empty, or a field that is neither a relation nor embedded."""
    if answered == "":
        return ""
    if not isinstance(answered, str) or answered not in annotations:
        raise exceptions.ProgrammingError(
            f"{owner}.DEFAULT_DISPLAY answers {answered!r}, which is not a field of {owner}"
        )
    kind = logical_type(annotations[answered])
    if kind.is_relational or kind in (FieldType.EMBEDDED, FieldType.UNKNOWN):
        raise exceptions.ProgrammingError(
            f"{owner}.DEFAULT_DISPLAY answers {answered}, a {kind} field: what is shown beside "
            f"the identity is a value of the record itself"
        )
    return answered


def _order_checked(
    owner: str, answered: object, annotations: dict[str, Any]
) -> tuple[Sort, ...]:
    """`DEFAULT_ORDER`: `Sort`s over fields of the entity."""
    entries = tuple(answered) if isinstance(answered, (list, tuple)) else (answered,)
    for entry in entries:
        if not isinstance(entry, Sort):
            raise exceptions.ProgrammingError(
                f'{owner}.DEFAULT_ORDER answers Sort, as in (Sort(field="code"),); '
                f"it was given {entry!r}"
            )
        if entry.field not in annotations:
            raise exceptions.ProgrammingError(
                f"{owner}.DEFAULT_ORDER orders by {entry.field}, which is not a field of {owner}"
            )
    return cast(tuple[Sort, ...], entries)


def _reading_checked(
    owner: str, answered: object, annotations: dict[str, Any]
) -> Criteria | None:
    """`DEFAULT_READING`: a `Specification` naming fields of the entity, as the criteria a read
    starts from; `None` is every scalar and no relation.

    Only the first level is checked here: below it a node names the related entity's fields,
    which a store answers or reports as dropped (rule 7 of the specification).
    """
    if answered is None:
        return None
    if not isinstance(answered, Specification):
        raise exceptions.ProgrammingError(
            f"{owner}.DEFAULT_READING answers a Specification — what of each record, never "
            f"which records —; it was given {type(answered).__name__}"
        )
    unknown = sorted(set(answered.root) - set(annotations))
    if unknown:
        raise exceptions.ProgrammingError(
            f"{owner}.DEFAULT_READING names {', '.join(unknown)}, "
            f"which {'is' if len(unknown) == 1 else 'are'} not a field of {owner}"
        )
    return Criteria(specification=answered)


def _search_checked(
    owner: str, answered: object, annotations: dict[str, Any]
) -> Criteria | None:
    """`DEFAULT_LITERAL_SEARCH`: a template with only a `where`, holding `TEXT` at least once,
    each condition on a field that can answer its operator.

    A prefix asks a text; containment asks a text or a translated text. Anything else would be
    dropped by the store and widen the search, so it is refused here.
    """
    if answered is None:
        return None
    if not isinstance(answered, Criteria):
        raise exceptions.ProgrammingError(
            f"{owner}.DEFAULT_LITERAL_SEARCH answers a Criteria whose where holds TEXT; "
            f"it was given {type(answered).__name__}"
        )
    extra = sorted(answered.model_fields_set - {"where"})
    if extra:
        raise exceptions.ProgrammingError(
            f"{owner}.DEFAULT_LITERAL_SEARCH sets {', '.join(extra)}: a template is a where "
            f"alone — the page, the order and what each record brings are the read's own"
        )
    conditions = conditions_of(answered.where)
    if not any(holds_text(one) for one in conditions):
        raise exceptions.ProgrammingError(
            f"{owner}.DEFAULT_LITERAL_SEARCH never uses TEXT: a template says where the typed "
            f'text goes, as in Condition(field="name", operator=Operator.LIKE, value=TEXT)'
        )
    for one in conditions:
        if one.field not in annotations:
            raise exceptions.ProgrammingError(
                f"{owner}.DEFAULT_LITERAL_SEARCH searches {one.field}, "
                f"which is not a field of {owner}"
            )
        if not holds_text(one):
            continue
        kind = logical_type(annotations[one.field])
        if (one.operator is Operator.STARTS_WITH and kind is not FieldType.TEXT) or (
            one.operator is Operator.LIKE and kind not in TEXT_TYPES
        ):
            raise exceptions.ProgrammingError(
                f"{owner}.DEFAULT_LITERAL_SEARCH searches {one.field} by "
                f"{one.operator.value}, which a {kind} field cannot answer"
            )
    return answered


def _while(
    owner: str, answered: object, part: str, annotations: dict[str, Any]
) -> dict[str, Expression]:
    """Each field a `When` covers, with its condition. A field covered twice is covered while
    either holds. The condition is a criteria's own, over fields the entity has.

        [When(state = draft, a.partner_id), When(locked = true, a.partner_id, a.date)]
        ->  {"partner_id": Any([state = draft, locked = true]), "date": locked = true}

    1. Each entry must be a `When` whose condition is a `Condition`, `All`, `Any` or `Not` —
       anything else is refused with the spelling that works.
    2. Every field the condition asks about must be a field of the entity.
    3. Each covered field collects the conditions that cover it.
    Final: one condition per field — itself alone, or an `Any` of all of them.
    """
    covered: dict[str, list[Expression]] = {}
    for entry in _entries(answered):
        if not isinstance(entry, When):
            raise exceptions.ProgrammingError(
                f"{owner}.presentation {part} takes When, as in "
                f"lambda a: [When(Condition.on(a.state, Operator.EQ, State.DRAFT), a.partner_id)]; "
                f"it was given {entry!r}"
            )
        if not isinstance(entry.condition, (Condition, All, Any_, Not)):
            raise exceptions.ProgrammingError(
                f"{owner}.presentation {part} takes a condition as Condition, All, Any or Not, "
                f"as in When(Condition.on(a.state, Operator.EQ, State.DRAFT), a.partner_id); "
                f"it was given {entry.condition!r}"
            )
        for leaf in conditions_of(entry.condition):
            if leaf.field not in annotations:
                raise exceptions.ProgrammingError(
                    f"{owner}.presentation {part} asks about {leaf.field}, "
                    f"which is not a field of {owner}"
                )
        for one in entry.fields:
            covered.setdefault(field_name(one, "When(..., a.partner_id)"), []).append(
                entry.condition
            )
    return {
        name: conditions[0] if len(conditions) == 1 else Any_(any=conditions)
        for name, conditions in covered.items()
    }


def _named(answered: object, form: str) -> frozenset[str]:
    """The fields a lambda listed."""
    return frozenset(field_name(one, form) for one in _entries(answered))


def framework_fields(declared: type) -> frozenset[str]:
    """The fields a framework class declares — `Entity`'s, a mixin's: written by the framework,
    so a form shows them read-only. Found by the class that declares each one, never by name.

        framework_fields(Invoice)       # class Invoice(Entity)
            ->  frozenset({"id", "created_at", "updated_at", "version",
                           "derivations", "presentation"})
        framework_fields(Client)        # class Client(ArchivableMixin, Entity)
            ->  the same, plus "archived_at"

    Context: every annotation of a framework class counts, its class variables
    (`derivations`, `presentation`) included — a caller intersects with the fields it has.
    """
    found: set[str] = set()
    for owner in declared.__mro__:
        if owner.__module__.startswith("sincpro_framework."):
            found.update(inspect.get_annotations(owner))
    return frozenset(found)


def _required_by_structure(declared: type, system: frozenset[str]) -> frozenset[str]:
    """The fields a record cannot be built without: no default, no factory, not optional."""
    if not dataclasses.is_dataclass(declared):
        return frozenset()
    annotations = annotations_of(declared)
    return frozenset(
        one.name
        for one in dataclasses.fields(declared)
        if one.name not in system
        and one.name in annotations
        and one.default is dataclasses.MISSING
        and one.default_factory is dataclasses.MISSING
        and annotations[one.name] == without_optional(annotations[one.name])
    )


@cache
def defaults_of(declared: type) -> dict[str, Any]:
    """What a new record of `declared` starts with, as JSON: each plain dataclass default.

        # class Invoice(Entity): currency_id = "BOB"; state = InvoiceState.DRAFT;
        #                        lines = field(default_factory=list); note: str | None = None
        defaults_of(Invoice)   ->  {"version": 0, "currency_id": "BOB", "state": "draft"}
        defaults_of(dict)      ->  {}     # not a dataclass

    1. A class that is not a dataclass has no defaults to offer.
    2. Each field, in declaration order:
       2.1 no plain default, or `None` — skipped; a factory is never run here;
       2.2 the default written as JSON by its field's annotation;
       2.3 a default nothing can write as JSON is skipped, left for the client.
    Final: the defaults, by field name — read once per class.
    """
    if not dataclasses.is_dataclass(declared):
        return {}
    annotations = annotations_of(declared)
    found: dict[str, Any] = {}
    for one in dataclasses.fields(declared):
        if one.default is dataclasses.MISSING or one.default is None:
            continue
        try:
            found[one.name] = TypeAdapter(annotations.get(one.name, Any)).dump_python(
                one.default, mode="json", warnings=False
            )
        except (PydanticSerializationError, PydanticSchemaGenerationError, ValueError):
            continue  # a default nothing can write as JSON is left for the client
    return found


@cache
def presentation_of(declared: type) -> Presented:
    """What the class answers about how it is read and shown, read once and checked against
    its fields.

        presentation_of(Account)    # DEFAULT_GET_ID "code", presentation required=[a.name]
            ->  Presented(get_id="code", display="name", order=(Sort(code),), …,
                          readonly={"id", "created_at", "updated_at", "version"},
                          required={"name", …})
        presentation_of(Money)      # a value object: no DEFAULT_*, no presentation
            ->  Presented()

    1. How it is read comes from its `DEFAULT_*` class methods (every `Entity` has them, with
       their defaults); a class without them — a value object, a plain model — answers none.
    2. Each answer is checked: a key, a display and an order over fields of its own, a reading
       that is a `Specification`, a literal search that is a template holding `TEXT`.
    3. The form hints come from its `presentation`, each lambda run once over `Fields`; an
       unknown name is refused, naming it. Left alone, the fields the framework writes are
       read-only and a field with no default that cannot be `None` is required.
    Final: the names, ready for `Meta`, `matching` and `detail_of`.
    """
    owner = declared.__name__
    annotations = annotations_of(declared)
    read: dict[str, Any] = {}
    if callable(getattr(declared, "DEFAULT_GET_ID", None)):
        read = {
            "get_id": _get_id_checked(owner, declared.DEFAULT_GET_ID(), annotations),
            "display": _display_checked(owner, declared.DEFAULT_DISPLAY(), annotations),
            "search": _search_checked(owner, declared.DEFAULT_LITERAL_SEARCH(), annotations),
            "order": _order_checked(owner, declared.DEFAULT_ORDER(), annotations),
            "detail": _reading_checked(owner, declared.DEFAULT_READING(), annotations),
        }
    presentation = getattr(declared, "presentation", None)
    if not isinstance(presentation, Presentation):
        return Presented(**read)
    fields = Fields(owner, annotations)
    system = framework_fields(declared)

    readonly = system & frozenset(annotations)
    if presentation.readonly is not None:
        readonly |= _named(presentation.readonly(fields), "a.number")
    required = _required_by_structure(declared, system)
    if presentation.required is not None:
        required |= _named(presentation.required(fields), "a.partner_id")

    return Presented(
        **read,
        readonly=readonly,
        required=required,
        readonly_when=(
            _while(owner, presentation.readonly_when(fields), "readonly_when", annotations)
            if presentation.readonly_when
            else {}
        ),
        required_when=(
            _while(owner, presentation.required_when(fields), "required_when", annotations)
            if presentation.required_when
            else {}
        ),
        visible_when=(
            _while(owner, presentation.visible_when(fields), "visible_when", annotations)
            if presentation.visible_when
            else {}
        ),
    )


def hinted(fields: dict[str, "FieldMeta"], declared: type) -> dict[str, "FieldMeta"]:
    """`fields`, each carrying the form hints the class declared or its structure implies:
    read-only, required, the default and the three conditions."""
    presented = presentation_of(declared)
    defaults = defaults_of(declared)
    return {
        name: meta.model_copy(
            update={
                "readonly": name in presented.readonly or meta.computed,
                "required": name in presented.required,
                "default": None if meta.computed else defaults.get(name),
                "readonly_when": presented.readonly_when.get(name),
                "required_when": presented.required_when.get(name),
                "visible_when": presented.visible_when.get(name),
            }
        )
        for name, meta in fields.items()
    }


@dataclasses.dataclass(frozen=True)
class Derivation:
    """One `Derive`, read: field names in place of lambdas."""

    field: str
    depends: tuple[str, ...]
    by: Any


def _derivation_order(owner: str, read: dict[str, Derivation]) -> list[str]:
    """The derived fields, each after the derived fields it reads (Kahn, declaration order among
    the free ones). A cycle is refused, naming it.

        total ← (subtotal, discount) · subtotal ← (lines,)   ->  ["subtotal", "total"]

    1. For each derived field, the other derived fields it reads — what it waits for; a
       field read from outside the derivations waits for nothing.
    2. A field that reads itself is refused.
    3. Round by round, every field waiting for nothing is placed and freed from the rest.
    Final: a round with nothing free is a cycle, refused naming every field still waiting.
    """
    waiting = {
        name: {one for one in derivation.depends if one in read and one != name}
        for name, derivation in read.items()
    }
    for name, derivation in read.items():
        if name in derivation.depends:
            raise exceptions.ProgrammingError(
                f"{owner}.derivations computes {name} from itself"
            )
    ordered: list[str] = []
    while waiting:
        free = [name for name, needs in waiting.items() if not needs]
        if not free:
            cycle = " → ".join(sorted(waiting))
            raise exceptions.ProgrammingError(
                f"{owner}.derivations depend on each other in a cycle: {cycle}"
            )
        for name in free:
            ordered.append(name)
            del waiting[name]
        for needs in waiting.values():
            needs.difference_update(free)
    return ordered


@cache
def derivations_of(declared: type) -> tuple[Derivation, ...]:
    """The class's `derivations`, read once, checked against its fields and put in the order
    they have to run.

        # Invoice: total ← (subtotal, discount), subtotal ← (lines,)
        derivations_of(Invoice)
            ->  (Derivation(subtotal, (lines,)), Derivation(total, (subtotal, discount)))
        derivations_of(Box)    ->  ()     # declares no derivations

    1. A class with no `Derivations` computes nothing.
    2. The lambda runs once over `Fields`; an unknown name is refused, naming it.
       2.1 An entry that is not a `Derive`, or whose `by` is not callable, is refused.
    3. A field derived twice is refused: two computations would race for it.
    4. Ordered so a field is computed after every derived field it reads; among the free ones,
       the order they were declared in.
       4.1 A cycle has no order and is refused, naming the fields in it.
    Final: the derivations, ready for `recompute`.
    """
    declaration = getattr(declared, "derivations", None)
    if not isinstance(declaration, Derivations) or declaration.declared is None:
        return ()
    owner = declared.__name__
    fields = Fields(owner, annotations_of(declared), "derivations")
    read: dict[str, Derivation] = {}
    for entry in _entries(declaration.declared(fields)):
        if not isinstance(entry, Derive):
            raise exceptions.ProgrammingError(
                f"{owner}.derivations takes Derive, as in lambda i: "
                f"[Derive(i.total, depends=i.subtotal, by={owner}.total_of)]; "
                f"it was given {entry!r}"
            )
        name = field_name(entry.field, "Derive(i.total, ...)")
        if name in read:
            raise exceptions.ProgrammingError(f"{owner}.derivations computes {name} twice")
        if not callable(entry.by):
            raise exceptions.ProgrammingError(
                f"{owner}.derivations computes {name} by {entry.by!r}, which is not callable"
            )
        read[name] = Derivation(
            field=name,
            depends=tuple(
                field_name(one, "Derive(..., depends=i.lines)")
                for one in _entries(entry.depends)
            ),
            by=entry.by,
        )
    return tuple(read[name] for name in _derivation_order(owner, read))


def default_order_of(declared: type, identity: str) -> str:
    """The order a list takes when nobody asks: `DEFAULT_ORDER`, or newest identity first for
    a class that has none.

    in      Account (DEFAULT_ORDER=code), "id"   →  out  "code"
    in      Note (Entity's default), "id"        →  out  "-id"
    """
    declared_order = ",".join(str(sort) for sort in presentation_of(declared).order)
    return declared_order or (f"-{identity}" if identity else "")


EVENT_ENVELOPE = frozenset({"entity_type", "entity_id", "correlation_id", "causation_id"})
"""The fields that make a class a domain event's: one carrying them also answers its `name`."""


def with_computed(fields: dict[str, FieldMeta], declared: type) -> dict[str, FieldMeta]:
    """The fields with the computed ones marked. `hinted` then shows them read-only.

    in      {'balance': …}, Invoice (balance computed)   →  out  balance: computed
    """
    marked = computations_of(declared)
    if not marked:
        return fields
    return {
        name: (field.model_copy(update={"computed": True}) if name in marked else field)
        for name, field in fields.items()
    }


def describe_class(declared: type, identity: str = "") -> "Meta":
    """A definition read off the annotations alone, with no table behind it.

        in      Shape(width: int, height: int, unit: str = "cm")
        out     Meta(aggregate='Shape', identity='', fields={width, height, unit})

    What a value object is described by, and what a repository with no database reads to
    validate a criteria. `identity` is empty for a value object, which has none, and the
    aggregate's own identity field when a record has one.
    """
    annotations = annotations_of(declared)
    translator = getattr(declared, "translations", None)
    name: Translated = (
        cast(Translated, translator())
        if callable(translator)
        else {"default": declared.__name__}
    )
    fields = _worded(
        {
            field_name: FieldMeta.for_column(
                logical_type(annotation),
                annotation != without_optional(annotation),
                members_of(annotation),
                without_optional(annotation) is Decimal,
                choice_labels=choice_translations_of(annotation),
            )
            for field_name, annotation in annotations.items()
            if related_class(annotation)[0] is None
            or logical_type(annotation) is not FieldType.UNKNOWN
        },
        declared,
    )
    if EVENT_ENVELOPE <= set(annotations) and isinstance(
        getattr(declared, "name", None), str
    ):
        # A domain event's wire name is a class attribute, not a field; every row holds it.
        fields["name"] = FieldMeta.for_column(FieldType.TEXT, False, [], False)
    fields = hinted(with_computed(fields, declared), declared)
    presented = presentation_of(declared)
    return Meta(
        aggregate=declared.__name__,
        identity=identity,
        default_order=default_order_of(declared, identity),
        get_id=presented.get_id,
        display=presented.display,
        search=presented.search,
        detail=presented.detail,
        fields=fields,
        name=name,
    )


FieldMeta.model_rebuild()
