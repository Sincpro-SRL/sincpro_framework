"""The filter language: a condition over one field, and the groups that combine conditions.

`Condition`, `All`, `Any_` and `Not` are the tree; `Expression` is any of them. What is read of a
tree — `combined`, `conditions_of`, `with_text` — lives here, and how it is answered lives beside it:
`entrypoint/evaluation.py` in memory, the ORM's translator in SQL.
"""

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Union

from pydantic import Discriminator, JsonValue, Tag

from sincpro_framework.ddd import exceptions
from sincpro_framework.ddd.criteria.domain.strict import Strict, refused_or_dropped
from sincpro_framework.ddd.criteria.domain.values import TEXT, Dynamic, item_kind, value_kind


class Operator(StrEnum):
    """How a question is asked. **The enum's value is how it is written outside**, so the
    definition publishes these same symbols in `ops` and no translation table exists anywhere.
    """

    EQ = "="
    NE = "!="
    IN = "in"
    NOT_IN = "not in"
    LIKE = "like"
    STARTS_WITH = "starts with"
    GT = ">"
    GTE = ">="
    LT = "<"
    LTE = "<="
    IS_NULL = "is null"
    CONTAINS = "contains"
    NOT_CONTAINS = "not contains"
    BETWEEN = "between"


MULTI_VALUED: frozenset[Operator] = frozenset(
    {Operator.IN, Operator.NOT_IN, Operator.BETWEEN}
)


Literal = JsonValue | datetime | date | Decimal

type Item = Annotated[
    Annotated[Dynamic, Tag("dynamic")] | Annotated[Literal, Tag("literal")],
    Discriminator(item_kind),
]
"""One value of a condition: a literal, or a dynamic value (`{"$": "today"}`) read when the filter
is used. An object with the reserved key `"$"` is dynamic; any other is a literal."""

type Value = Annotated[
    Annotated[Sequence[Item], Tag("list")] | Annotated[Item, Tag("item")],
    Discriminator(value_kind),
]
"""What a condition compares: an item, or a list of them — `code in [<text typed>, "0"]`."""


class Condition(Strict):
    """One question about one field.

    `value` is required: a condition without a value is not a condition, and a `None` default
    let `Condition(field="name")` build happily, meaning "name equals nothing".

    It is `JsonValue` and not `Any`: what a condition compares is a value that travelled in a
    JSON — a number, a text, a boolean, a list of them — and `Any` let any Python object
    through until the SQL translator tripped over it, far from here.

    `operator` does have a default, and that is a different case: omitting it means equality.

    Beside JSON it takes `datetime`, `date` and `Decimal`, because a Feature also builds
    conditions from Python and forcing it to serialise a date to text for the model to read
    back was a detour. In `model_dump(mode="json")` they come out as text all the same.
    """

    field: str
    value: Value
    operator: Operator = Operator.EQ

    @classmethod
    def on(cls, field: Any, operator: Operator, value: Value) -> "Condition":
        """A condition whose field is read through an entity, so a type checker follows it.

            presentation = Presentation["Invoice"](
                readonly_when=lambda i: When(
                    Condition.on(i.state, Operator.NE, InvoiceState.DRAFT), i.partner_id
                ),
            )

        Inside such a lambda `i.state` is typed as the field's value, which `field: str` refuses;
        this takes it as the name it stands for. A value that is not a text is refused.
        """
        if not isinstance(field, str):
            raise exceptions.InvalidCriteria(
                f"a condition is on a field read through the entity, as in "
                f"Condition.on(i.state, Operator.EQ, State.DRAFT); it was given {field!r}"
            )
        return cls(field=str(field), operator=operator, value=value)


class All(Strict):
    all: list["Expression"]


class Any_(Strict):
    any: list["Expression"]


class Not(Strict):
    negate: "Expression"


Expression = Union[Condition, All, Any_, Not]

All.model_rebuild()
Any_.model_rebuild()
Not.model_rebuild()


GROUPS = {"any", "all", "negate"}


def expression_from(value: Any, tolerant: bool = False) -> Any:
    """A filter as the tree the engine reads.

        in   {"field": "row_count", "operator": ">", "value": 1000}
        out  Condition(field='row_count', operator=GT, value=1000)

        in   {"any": [{...}, {...}]}      →  Any_([Condition(…), Condition(…)])
        in   {"negate": {...}}            →  Not(Condition(…))

    **A condition is an object with `field`, `operator` and `value`; a group is an object with
    `all`, `any` or `negate`.** There is no positional or abbreviated form: what arrives is
    what the type declares, and the only work here is building the nodes of the tree.

    Recursive, because the children of a group are trees: the first version looked only at
    the top level, the branches of an `any` stayed raw, pydantic dropped the whole filter and
    a listing that asked for two origins showed all of them.
    """
    if not isinstance(value, dict):
        return value

    for group in ("any", "all"):
        if group in value:
            refused_or_dropped(value, {group}, tolerant)
            children = [expression_from(one, tolerant) for one in value[group]]
            return Any_(any=children) if group == "any" else All(all=children)
    if "negate" in value:
        refused_or_dropped(value, {"negate"}, tolerant)
        return Not(negate=expression_from(value["negate"], tolerant))
    if "field" in value:
        # Built, not handed back raw: left as a dict, a criteria did not survive its own
        # `model_dump`, so sending it in a POST or through a queue failed.
        return Condition(**refused_or_dropped(value, set(Condition.model_fields), tolerant))

    raise exceptions.InvalidCriteria(
        "a filter is {'field', 'operator', 'value'} or an object with 'all', 'any' or 'negate'; "
        f"got {sorted(value)}"
    )


def combined(*expressions: Expression | None) -> Expression | None:
    """Context: the `and` of whatever is not empty, kept flat.

    Criteria get merged repeatedly — a saved reading, what the user typed, what the interface
    always adds — and wrapping each merge in a new `All` nests the tree as deep as the number
    of merges for a query that is one `WHERE`.

    1. Drop the empties, and splice an incoming `All` instead of nesting it.
    2. Final: no parts is no filter, one part needs no wrapper, several become an `All`.
    """
    parts: list[Expression] = []
    for expression in expressions:
        if expression is None:
            continue
        if isinstance(expression, All):
            parts.extend(expression.all)
        else:
            parts.append(expression)

    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    return All(all=parts)


def conditions_of(expression: Expression | None) -> list[Condition]:
    """Every leaf of a tree, in reading order.

    in   All([Any_([Condition(a), Condition(b)]), Not(Condition(c))])
    out  [Condition(a), Condition(b), Condition(c)]
    in   None  →  out  []
    """
    match expression:
        case None:
            return []
        case Condition():
            return [expression]
        case All():
            return [leaf for part in expression.all for leaf in conditions_of(part)]
        case Any_():
            return [leaf for part in expression.any for leaf in conditions_of(part)]
        case Not():
            return conditions_of(expression.negate)


def holds_text(condition: Condition) -> bool:
    """Whether a template's condition waits for the literal: its value is `TEXT`, or a list
    holding it (`code in [TEXT, "0"]`)."""
    value = condition.value
    return value == TEXT or (isinstance(value, list) and TEXT in value)
