"""The filter language: a condition over one field, and the groups that combine conditions.

`Condition`, `All`, `Any_` and `Not` are the tree, and `Node` is what they share: what is read of a
tree — its conditions, its canonical form, a copy with its leaves changed — is a method of `Node`.
`Expression` is any of the four, the type a field declares so pydantic reads the right one. How a
tree is answered lives elsewhere: `services/evaluation.py` in memory, the ORM's translator in SQL.
"""

import json
from collections.abc import Callable, Sequence
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Union

from pydantic import Discriminator, JsonValue, Tag

from sincpro_framework.ddd import exceptions
from sincpro_framework.ddd.criteria.domain.strict import Strict
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


def _one_spelling(value: Any) -> Any:
    """A condition's value in one spelling: a decimal normalised, a list sorted.

        Decimal("1.00")   ->  Decimal("1")
        [3, 1, "a"]       ->  ["a", 1, 3]     sorted by JSON text, so mixed types compare

    Context: `in [3, 1]` and `in [1, 3]` ask for the same rows, so they must key the same.
    """
    if isinstance(value, Decimal):
        return value.normalize()
    if isinstance(value, list):
        return sorted(
            (_one_spelling(one) for one in value),
            key=lambda one: json.dumps(one, default=str),
        )
    return value


def _in_order(parts: Any) -> list[Any]:
    """The parts of an `all` or an `any`, sorted by their JSON — any total order will do, as
    long as it is the same for the same parts."""
    return sorted(
        parts, key=lambda part: json.dumps(part.model_dump(mode="json"), sort_keys=True)
    )


class Node(Strict):
    """What every part of a filter tree is — a `Condition`, an `All`, an `Any_` or a `Not` — and
    what is read of a tree without answering it.

        tree = All(all=[Condition(field="a", value=1), Not(negate=Condition(field="b", value=2))])
        tree.conditions()                   ->  [a = 1, b = 2]
        tree.mapped(lambda leaf: None)      ->  None
        Node.combined(None, tree)           ->  tree
        Node.read({"field": "a", "value": 1})   ->  Condition(field='a', value=1, ...)

    No docstring reaches the contract from here: pydantic publishes a class's own docstring, and
    the four nodes leave theirs out of `criteria.schema.json` on purpose.
    """

    @classmethod
    def read(cls, written: Any, tolerant: bool = False) -> Any:
        """A filter as the tree the engine reads.

            in   {"field": "row_count", "operator": ">", "value": 1000}
            out  Condition(field='row_count', operator=GT, value=1000)

            in   {"any": [{...}, {...}]}      →  Any_([Condition(…), Condition(…)])
            in   {"negate": {...}}            →  Not(Condition(…))
            in   {"through": "lines", "selection": {...}}   →  Through("lines", Condition(…))

        **A condition is an object with `field`, `operator` and `value`; a group is an object
        with `all`, `any` or `negate`.** There is no positional or abbreviated form: what arrives
        is what the type declares, and the only work here is building the nodes of the tree.

        Recursive, because the children of a group are trees: the first version looked only at
        the top level, the branches of an `any` stayed raw, pydantic dropped the whole filter
        and a listing that asked for two origins showed all of them.

        1. Anything but a dict — a node already built — is handed back as it is.
        2. `any` or `all` (`any` looked at first): a key beside it is refused — dropped when
           `tolerant` — and each child is read in turn into an `Any_` or an `All`.
        3. `negate`: the same rule for the other keys, its child read into a `Not`.
        4. `through`: the relation it crosses and its `selection`, read into a `Through`.
        5. `field`: a `Condition` of the keys it takes, the others refused or dropped.
        Final: a dict with none of those keys is `InvalidCriteria`, naming the keys it had.
        """
        if not isinstance(written, dict):
            return written

        if "any" in written:
            Any_.known_keys_of(written, tolerant)
            return Any_(any=[Node.read(one, tolerant) for one in written["any"]])
        if "all" in written:
            All.known_keys_of(written, tolerant)
            return All(all=[Node.read(one, tolerant) for one in written["all"]])
        if "negate" in written:
            Not.known_keys_of(written, tolerant)
            return Not(negate=Node.read(written["negate"], tolerant))
        if "through" in written:
            Through.known_keys_of(written, tolerant)
            return Through(
                through=written["through"],
                selection=Node.read(written.get("selection"), tolerant),
            )
        if "field" in written:
            # Built, not handed back raw: left as a dict, a criteria did not survive its own
            # `model_dump`, so sending it in a POST or through a queue failed.
            return Condition(**Condition.known_keys_of(written, tolerant))

        raise exceptions.InvalidCriteria(
            "a filter is {'field', 'operator', 'value'} or an object with 'all', 'any', "
            f"'negate' or 'through'; got {sorted(written)}"
        )

    @classmethod
    def combined(cls, *expressions: "Expression | None") -> "Expression | None":
        """Context: the `and` of whatever is not empty, flattened.

        Criteria get merged repeatedly — a saved reading, what the user typed, what the
        interface always adds — and wrapping each merge in a new `All` nests the tree as deep
        as the number of merges for a query that is one `WHERE`.

            Node.combined(None, a)                     ->  a
            Node.combined(All(all=[a, b]), None, c)    ->  All(all=[a, b, c])   # spliced
            Node.combined(Any_(any=[a, b]), c)         ->  All(all=[Any_(any=[a, b]), c])
            Node.combined(None, None)                  ->  None

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

    def conditions(self) -> "list[Condition]":
        """Every leaf of the tree over this record's own fields, in reading order.

            All([Any_([Condition(a), Condition(b)]), Not(Condition(c))]).conditions()
                ->  [Condition(a), Condition(b), Condition(c)]
            All([Condition(a), Through("lines", Condition(qty))]).conditions()
                ->  [Condition(a)]          qty is a field of a line, not of this record

        Context: a `Not` hands its leaf on as it is — what it negates is not this list's to say.
        A `Through` hands on none: its leaves name the fields of the records it crosses to.
        """
        match self:
            case Condition():
                return [self]
            case All():
                return [leaf for part in self.all for leaf in part.conditions()]
            case Any_():
                return [leaf for part in self.any for leaf in part.conditions()]
            case Not():
                return self.negate.conditions()
            case Through():
                return []
        raise exceptions.InvalidCriteria(f"not a filter expression: {self!r}")

    def canonical(self) -> "Expression":
        """The one form of a filter: its parts in a fixed order and its values in one spelling.

            All([b = 1, a in [3, 1]]).canonical() == All([a in [1, 3], b = 1]).canonical()

        What a filter means does not depend on the order of the parts of an `all` or an `any`,
        on the order of the items of an `in`, or on how many zeros a decimal was written with.
        Whatever has to tell two filters apart — the key of a cache — compares this form.

        >>> Condition(field="amount", value=Decimal("1.00")).canonical()
        Condition(field='amount', value=Decimal('1'), operator=<Operator.EQ: '='>)

        1. A condition: its value in one spelling.
        2. An `all` or an `any`: each part made canonical first, then the parts in a fixed order.
        3. A `not` makes canonical what it negates.
        Final: a `through` makes canonical its selection.
        """
        match self:
            case Condition():
                return self.model_copy(update={"value": _one_spelling(self.value)})
            case All():
                return All(all=_in_order(part.canonical() for part in self.all))
            case Any_():
                return Any_(any=_in_order(part.canonical() for part in self.any))
            case Not():
                return Not(negate=self.negate.canonical())
            case Through():
                return Through(through=self.through, selection=self.selection.canonical())
        raise exceptions.InvalidCriteria(f"not a filter expression: {self!r}")

    def mapped(
        self,
        leaf: "Callable[[Condition], Expression | None]",
        through: "Callable[[Through], Expression | None] | None" = None,
    ) -> "Expression | None":
        """The tree with each condition replaced by what `leaf` says, and what is left collapsed.

            in      All([a, b]), leaf = a → a2, b → None
            out     a2                           one part needs no wrapper

        The one way to rebuild a filter: filling a typed text, resolving a `today`, dropping
        what a model cannot answer — each decides only what becomes of ONE condition; what
        happens to the `all`, `any` and `not` around it is the same every time, and is here.

        A condition may become another expression (a range is two conditions) or nothing. A
        group that loses all of its parts disappears instead of becoming empty — an empty `all`
        matches everything and an empty `any` nothing, and neither was asked for — and a `not`
        of nothing is nothing.

        A `through` is another record's question: `leaf` never sees its conditions. `through`
        decides what becomes of it — checked against the related model, resolved, dropped — and
        without one it stays as it is.
        """
        match self:
            case Condition():
                return leaf(self)
            case All():
                parts = [
                    one for one in (part.mapped(leaf, through) for part in self.all) if one
                ]
                return All(all=parts) if len(parts) > 1 else (parts[0] if parts else None)
            case Any_():
                parts = [
                    one for one in (part.mapped(leaf, through) for part in self.any) if one
                ]
                return Any_(any=parts) if len(parts) > 1 else (parts[0] if parts else None)
            case Not():
                inner = self.negate.mapped(leaf, through)
                return Not(negate=inner) if inner is not None else None
            case Through():
                return through(self) if through is not None else self
        raise exceptions.InvalidCriteria(f"not a filter expression: {self!r}")

    def with_text(self, text: str) -> "Expression | None":
        """A search template's filter with what a person typed in place of every `TEXT`, read
        word by word: each word has to appear in some field the template searches.

            in   Any([code starts with TEXT, active = true]), "1.2"
            out  Any([code starts with "1.2", active = true])
            in   Any([code starts with TEXT, name like TEXT]), "factura 2026"
            out  All([Any([code starts with "factura", name like "factura"]),
                      Any([code starts with "2026", name like "2026"])])
            in   All([name like TEXT, active = true]), "   "
            out  active = true                        a blank text asks nothing of the text

        1. The text split on whitespace: a code such as `T-1` or `1.2.3` stays one word.
        2. Each word put where `TEXT` stood — the whole value, or each `TEXT` member of a list.
        3. No word: every condition holding `TEXT` is dropped, the rest of the template stays.
        Final: the template filled once per word, joined by `and`. The template never changes.
        """

        def filled(word: str) -> "Expression | None":
            """The template with one word where `TEXT` stood; no word drops what held it."""

            def leaf(condition: Condition) -> Condition | None:
                if not condition.holds_text():
                    return condition
                if not word:
                    return None
                value = condition.value
                return condition.model_copy(
                    update={
                        "value": (
                            [word if one == TEXT else one for one in value]
                            if isinstance(value, list)
                            else word
                        )
                    }
                )

            return self.mapped(leaf)

        words = text.split()
        return Node.combined(*(filled(word) for word in words)) if words else filled("")


class Condition(Node):
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

    def holds_text(self) -> bool:
        """Whether a template's condition waits for the literal: its value is `TEXT`, or a list
        holding it (`code in [TEXT, "0"]`)."""
        return self.value == TEXT or (isinstance(self.value, list) and TEXT in self.value)


# No class docstring: pydantic publishes it as the description in the committed
# `criteria.schema.json` contract, which the TypeScript package copies.
# Every part holds — `{"all": [...]}` on the wire, AND in SQL.
class All(Node):
    all: list["Expression"]


# At least one part holds — `{"any": [...]}` on the wire, OR in SQL. The underscore only
# separates it from `typing.Any`; it is exported as `Any`.
class Any_(Node):
    any: list["Expression"]


# The part does not hold — `{"negate": {...}}` on the wire.
class Not(Node):
    negate: "Expression"


# Some record the relation holds answers the selection — `{"through": "lines", "selection":
# {...}}` on the wire, a correlated EXISTS in SQL.
class Through(Node):
    through: str
    selection: "Expression"


Expression = Union[Condition, All, Any_, Not, Through]

All.model_rebuild()
Any_.model_rebuild()
Not.model_rebuild()
Through.model_rebuild()
