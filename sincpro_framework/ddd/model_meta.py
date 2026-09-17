"""What a mapped model is, and everything only it can answer about a filter.

Derived by the adapter's `describe`, never declared. It reaches a client as `model_meta_data`
so a filter form can be built without anybody maintaining a schema.

The rules about what may be asked live here rather than in whoever is asking: a field knows
which operators it takes and how to read a value written as text, and the model knows which of
its fields can be ordered or grouped by. See `docs/persistence/reference.md`.

**Nothing here inspects a mapper.** This module says what a definition *is* and what it
accepts; reading one off a mapped class is the adapter's job, because that is the half that
knows the ORM.
"""

import sys
import types
from datetime import date, datetime
from decimal import Decimal
from enum import Enum, StrEnum
from typing import Any, Literal, Union, cast, get_args, get_origin, get_type_hints

from pydantic import computed_field

from sincpro_framework.ddd.criteria import (
    MULTI_VALUED,
    All,
    Any_,
    Condition,
    Expression,
    Not,
    Operator,
    Specification,
)
from sincpro_framework.ddd.entity import Translated
from sincpro_framework.ddd.entity_collection import Dropped
from sincpro_framework.ddd.exceptions import ContractViolation, InvalidCriteria
from sincpro_framework.sincpro_abstractions import DataTransferObject

TRUTHY = frozenset({"1", "true", "yes", "on", "t"})


class Unreadable(ValueError):
    """A value that will not become the type its field holds."""


class FieldType(StrEnum):
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
    UNKNOWN = "unknown"

    @property
    def is_relational(self) -> bool:
        """Whether a field of this type points at another aggregate, one with its own life,
        that has to be brought: the three Odoo words say the cardinality and the side of the key.

        >>> FieldType.MANY2ONE.is_relational, FieldType.EMBEDDED.is_relational
        (True, False)
        """
        return self in (FieldType.MANY2ONE, FieldType.ONE2MANY, FieldType.MANY2MANY)

    def read(self, raw: Any) -> Any:
        """Reads a value written as text into what this type holds.

            in      "1000" as INTEGER          the text a URL carries
            out     1000

        Anything not text is already read and comes back untouched. A text that will not
        convert raises `Unreadable`, which the model turns into a dropped condition.

        >>> FieldType.INTEGER.read("1000")
        1000
        >>> FieldType.DATETIME.read("2026-03-14T15:09:26")
        datetime.datetime(2026, 3, 14, 15, 9, 26)
        >>> FieldType.BOOLEAN.read("yes")
        True
        >>> FieldType.INTEGER.read("banana")
        Unreadable: invalid literal for int() with base 10: 'banana'
        """
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
                case _:
                    return raw
        except ValueError as error:
            raise Unreadable(str(error)) from error


COMPARABLE: tuple[Operator, ...] = (
    Operator.EQ,
    Operator.NE,
    Operator.GT,
    Operator.GTE,
    Operator.LT,
    Operator.LTE,
    Operator.BETWEEN,
)

OPERATORS_BY_TYPE: dict[FieldType, tuple[Operator, ...]] = {
    FieldType.TEXT: (Operator.EQ, Operator.NE, Operator.LIKE, Operator.IN, Operator.NOT_IN),
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
    """The class's annotations with forward references resolved against its module.

        in  Dataset  →  out  {'dataset_id': str, 'row_count': int, …}

    A reference that does not resolve fails here, at boot, and names the class: a typo in a
    forward reference should not surface as a missing relation at the first request.
    """
    try:
        return get_type_hints(declared, vars(sys.modules[declared.__module__]))
    except NameError as error:
        raise ContractViolation(
            f"{declared.__name__} names a type that cannot be resolved at runtime: {error}"
        ) from error


def members_of(annotation: Any) -> list[Any]:
    """The values an enum field may hold, in declaration order; empty when it is not one."""
    members = enum_of(annotation)
    return [] if members is None else [member.value for member in members]


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
    words: Translated = (
        cast(Translated, translator())
        if callable(translator)
        else {"name": {"default": declared.__name__}, "labels": {}}
    )
    fields = {
        name: FieldMeta.for_column(
            logical_type(annotation),
            annotation != without_optional(annotation),
            members_of(annotation),
        )
        for name, annotation in annotations.items()
        if related_class(annotation)[0] is None
        or logical_type(annotation) is not FieldType.UNKNOWN
    }
    return Meta(
        aggregate=declared.__name__,
        identity=identity,
        default_order=f"-{identity}" if identity else "",
        fields=fields,
        translations=words,
    )


def related_class(annotation: Any) -> tuple[type | None, bool]:
    """The class an annotation points at, and whether it names several of them.

    in  list[Run]    →  out  (Run, True)
    in  Shelf | None →  out  (Shelf, False)
    in  str          →  out  (str, False)      a plain type is still a class
    in  dict[str, str] → out (None, False)
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


def enum_of(annotation: Any) -> type[Enum] | None:
    """The enum an annotation names, if it names one.

    in  Stage | None   →  out  Stage
    in  str            →  out  None
    """
    base = without_optional(annotation)
    return base if isinstance(base, type) and issubclass(base, Enum) else None


class FieldMeta(DataTransferObject):
    type: FieldType
    nullable: bool

    choices: list[Any] = []
    """The values an enum field may hold — what a select is built from."""

    sortable: bool
    """False for every nullable column: a keyset comparison omits rows holding NULL, so
    ordering by one loses rows from the pagination in silence."""

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
    definition: "Meta | None" = None
    """The shape of the other side: always for an embedded value, and for a relational field
    once it was expanded, cut by the same mask."""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def kind(self) -> Literal["scalar", "embedded", "relation"]:
        """The coarse answer to «what is this name», before the finer `type`: a value in the
        row, a value object inside the row, or another aggregate to bring. Travels with the
        definition so a client branches on one word.

        >>> describe(Work).fields["author"].kind, describe(Work).fields["size"].kind
        ('relation', 'embedded')
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
    ) -> "FieldMeta":
        """The definition of one column, from what the reader knows about it.

            in      INTEGER, nullable=False   →  sortable, every comparable operator
            in      TEXT, nullable=True       →  not sortable, and `is null` offered too

        1. Offer the operators its type takes, plus `is_null` when it may be absent.
        2. List the values of an enum as its choices.
        3. Final: sortable only when NOT NULL and not a list or a translation — a keyset
           comparison drops rows holding NULL, and there is no order over a JSON value a
           cursor could page through.
        """
        unsortable = (FieldType.TEXT_LIST, FieldType.TRANSLATED)
        return cls(
            type=logical,
            nullable=nullable,
            choices=list(members or []),
            sortable=not nullable and logical not in unsortable,
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

    def read(self, condition: Condition) -> Condition:
        """Context: the same condition with its value in the type this column compares against.

        >>> row_count.read(Condition(field="row_count", value="1000", operator=Operator.GT))
        Condition(field='row_count', value=1000, operator=<Operator.GT: '>'>)


        1. `is_null` is the exception: its value is the question asked, not the field's type.
        2. A multi-valued operator reads every member.
        3. Final: `contains` asks about one member, so its value is a member's type.
        """
        if condition.operator is Operator.IS_NULL:
            return condition.model_copy(
                update={"value": FieldType.BOOLEAN.read(condition.value)}
            )

        if condition.operator is Operator.BETWEEN:
            given = condition.value
            if not isinstance(given, list) or len(given) != 2:
                raise Unreadable(
                    f"'between' compares against two bounds; got {condition.value!r}"
                )
            return condition.model_copy(
                update={"value": [self.type.read(one) for one in given]}
            )

        if condition.operator in MULTI_VALUED:
            given = (
                condition.value if isinstance(condition.value, list) else [condition.value]
            )
            return condition.model_copy(
                update={"value": [self.type.read(one) for one in given]}
            )

        if self.type is FieldType.TEXT_LIST:
            # `contains` asks about one member, so its value is a member's type; `eq` and `ne`
            # ask about the whole list and take it as it came.
            if condition.operator in (Operator.EQ, Operator.NE):
                return condition
            return condition.model_copy(
                update={"value": FieldType.TEXT.read(condition.value)}
            )

        return condition.model_copy(update={"value": self.type.read(condition.value)})


class Meta(DataTransferObject):
    """What a caller is told about an aggregate: one map of fields, scalar, embedded and
    relational alike, each saying with `type` which it is. The shape of Odoo's `fields_get`.
    """

    aggregate: str
    identity: str
    default_order: str
    fields: dict[str, FieldMeta]
    translations: Translated
    """Every word a screen shows for this aggregate and its fields, exactly as the class's
    `translations()` answered it."""

    @property
    def relations(self) -> dict[str, FieldMeta]:
        """The relational fields alone, a view over `fields`: what may be expanded.

        >>> set(describe(Dataset).relations)
        {'runs', 'producer'}
        """
        return {
            name: field for name, field in self.fields.items() if field.type.is_relational
        }

    def _kept(self, condition: Condition) -> tuple[Condition | None, Dropped | None]:
        """One condition, kept and read — or dropped with the reason.

            in      Condition(row_count, "1000", GT)
            out     (Condition(row_count, 1000, GT), None)          it fits
            or      (None, Dropped(field='gone', reason='unknown_field'))

        Three things can be wrong and they are not the same: an unknown field, an operator the
        type does not take, and a value that will not read.
        """
        field = self.fields.get(condition.field)
        if field is None:
            return None, Dropped(field=condition.field, reason="unknown_field")

        if not field.accepts(condition.operator):
            return None, Dropped(field=condition.field, reason="unsupported_operator")

        try:
            return field.read(condition), None
        except Unreadable:
            return None, Dropped(field=condition.field, reason="bad_value")

    def _pruned(self, expression: Expression, dropped: list[Dropped]) -> Expression | None:
        """The tree with every unusable leaf removed, collapsing whatever is left empty.

            in      All([Condition(name, LIKE), Condition(gone, EQ)])
            keep    [Condition(name, LIKE)]              the second is unknown
            out     Condition(name, LIKE)                one part needs no wrapper

        A connective that loses all of its parts disappears rather than becoming empty — an
        empty `AND` matches everything and an empty `OR` matches nothing, and neither was
        asked for. Removing the node hands the decision to its parent.
        """
        match expression:
            case Condition():
                kept, loss = self._kept(expression)
                if loss is not None:
                    dropped.append(loss)
                return kept
            case All():
                parts = [p for p in (self._pruned(x, dropped) for x in expression.all) if p]
                return All(all=parts) if len(parts) > 1 else (parts[0] if parts else None)
            case Any_():
                parts = [p for p in (self._pruned(x, dropped) for x in expression.any) if p]
                return Any_(any=parts) if len(parts) > 1 else (parts[0] if parts else None)
            case Not():
                inner = self._pruned(expression.negate, dropped)
                return Not(negate=inner) if inner is not None else None

    def field(self, name: str) -> FieldMeta:
        """The field, or a refusal naming it. For the callers that cannot go on without one.

        >>> describe(Dataset).field("row_count").type
        <FieldType.INTEGER: 'integer'>
        >>> describe(Dataset).field("nope")
        InvalidCriteria: 'nope': no such field on dataset
        """
        found = self.fields.get(name)
        if found is None:
            raise InvalidCriteria(f"'{name}': no such field on {self.aggregate}")
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
            raise InvalidCriteria(
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

    def only(self, specification: Specification | None) -> "Meta":
        """This definition as the masked payload will look: the same shape, narrowed.

            in      {"name": {}}        →  out  fields={identity, name}
            in      nothing asked       →  out  every scalar and embedded field, no relation
            in      {"size": {"specification": {"width": {}}}}
                                        →  out  the embedded shape cut to {width}, identity kept

        **This is what makes the mask one mask.** A client that asked for three fields gets
        three in the payload AND three in the definition, so its filter builder and its column
        menu cannot offer what it was not given.

        The identity always stays: a record whose id did not travel cannot be opened,
        refreshed or cached, so masking it away makes the rest useless.
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
        for name in dict.fromkeys([self.identity, *specification.named]):
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
        self, expression: Expression | None
    ) -> tuple[Expression | None, list[Dropped]]:
        """The part of a filter this model can answer, and the list of what it could not.

        Nothing raises: an unusable condition is dropped and reported, because a shared link
        outlives the schema it was written against and a dropped filter always widens.

        >>> meta.accept(Condition(field="row_count", operator=Operator.GT, value="1000"))
        (Condition(field='row_count', operator=<Operator.GT>, value=1000), [])
        >>> meta.accept(Condition(field="gone", value="1"))
        (None, [Dropped(field='gone', reason='unknown_field')])
        """
        if expression is None:
            return None, []
        dropped: list[Dropped] = []
        return self._pruned(expression, dropped), dropped


FieldMeta.model_rebuild()
