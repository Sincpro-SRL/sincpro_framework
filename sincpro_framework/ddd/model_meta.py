"""What a mapped model is, and everything only it can answer about a filter.

Derived by the adapter's `describe`, never declared. It reaches a client as `model_meta_data`
so a filter form can be built without anybody maintaining a schema.

The rules about what may be asked live here rather than in whoever is asking: a field knows
which operators it takes and how to read a value written as text, and the model knows which of
its fields can be ordered or grouped by. See `docs/design/persistence.md` §6.

**Nothing here inspects a mapper.** This module says what a definition *is* and what it
accepts; reading one off a mapped class is the adapter's job, because that is the half that
knows the ORM.
"""

import types
from datetime import date, datetime
from decimal import Decimal
from enum import Enum, StrEnum
from typing import Any, Union, get_args, get_origin

from sincpro_framework.ddd.criteria import (
    MULTI_VALUED,
    All,
    Any_,
    Condition,
    Expression,
    Not,
    Operator,
)
from sincpro_framework.ddd.entity import Translated
from sincpro_framework.ddd.entity_collection import Dropped
from sincpro_framework.ddd.exceptions import InvalidCriteria
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
    UNKNOWN = "unknown"

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
        Condition(field='row_count', value=1000, operator=<Operator.GT: 'gt'>)


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


class RelationMeta(DataTransferObject):
    target: str
    many: bool


class Meta(DataTransferObject):
    aggregate: str
    identity: str
    default_order: str
    fields: dict[str, FieldMeta]
    relations: dict[str, RelationMeta] = {}
    translations: Translated
    """Every word a screen shows for this aggregate and its fields, exactly as the class's
    `translations()` answered it."""

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
