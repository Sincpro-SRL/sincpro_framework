"""What a caller sends: the filter, the ordering, the page.

One object and everything that goes inside it. A filter *is* a criteria — what the interface
calls a saved reading merges into this. See `docs/design/persistence.md` §3.

The filter language is deliberately small: conditions over one aggregate's own fields, combined
with and/or/not. No joins, no subqueries, no computed expressions — what this cannot say is
reached through the search engine's escape hatch.
"""

import json
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Union, cast

from pydantic import Field, JsonValue, field_validator

from sincpro_framework.ddd.exceptions import InvalidCriteria
from sincpro_framework.ddd.pagination import Cursor, Pagination
from sincpro_framework.sincpro_abstractions import DataTransferObject


class Operator(StrEnum):
    """How a question is asked. **The enum's value is how it is written outside**, so the
    definition publishes these same symbols in `ops` and no translation table exists anywhere.
    """

    EQ = "="
    NE = "!="
    IN = "in"
    NOT_IN = "not in"
    LIKE = "like"
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


class CountMode(StrEnum):
    NONE = "none"
    CAPPED = "capped"
    EXACT = "exact"


class Condition(DataTransferObject):
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
    value: JsonValue | datetime | date | Decimal
    operator: Operator = Operator.EQ


class All(DataTransferObject):
    all: list["Expression"]


class Any_(DataTransferObject):
    any: list["Expression"]


class Not(DataTransferObject):
    negate: "Expression"


Expression = Union[Condition, All, Any_, Not]

All.model_rebuild()
Any_.model_rebuild()
Not.model_rebuild()


EXPRESSION_KEYS = frozenset({"field", "all", "any", "negate"})


class Sort(DataTransferObject):
    field: str
    descending: bool = False

    def __str__(self) -> str:
        return f"-{self.field}" if self.descending else self.field


def expression_from(value: Any) -> Any:
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

    if "any" in value:
        return Any_(any=[expression_from(one) for one in value["any"]])
    if "all" in value:
        return All(all=[expression_from(one) for one in value["all"]])
    if "negate" in value:
        return Not(negate=expression_from(value["negate"]))
    if "field" in value:
        # Built, not handed back raw: left as a dict, a criteria did not survive its own
        # `model_dump`, so sending it in a POST or through a queue failed.
        return Condition(**value)

    raise InvalidCriteria(
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


def parse_order(raw: str) -> tuple[Sort, ...]:
    """Reads an ordering written the way a URL carries it.

        in      "-registered_at,name"
        split   ["-registered_at", "name"]      on commas, trimmed
        out     (Sort(registered_at, descending), Sort(name, ascending))

    >>> parse_order("-registered_at,name")
    (Sort(field='registered_at', descending=True), Sort(field='name', descending=False))
    >>> parse_order(" -a , , b ")                # blanks and empty parts are ignored
    (Sort(field='a', descending=True), Sort(field='b', descending=False))
    >>> parse_order("")
    ()
    """
    return tuple(
        Sort(field=part.lstrip("-"), descending=part.startswith("-"))
        for part in (piece.strip() for piece in raw.split(","))
        if part
    )


class Fold(DataTransferObject):
    """One number folded out of a bucket's rows.

        in      ["sum", "row_count"]
        out     Fold(function='sum', field='row_count')       → sum(row_count) AS <name>

    A pair and not `"sum:row_count"`, so nobody has to split a string — the same reason a
    condition is written as a triple.
    """

    function: str
    field: str

    @classmethod
    def read(cls, written: Any) -> "Fold":
        """A fold as it comes in JSON: the pair, and nothing else.

        >>> Fold.read(["sum", "row_count"])
        Fold(function='sum', field='row_count')
        """
        if isinstance(written, Fold):
            return written
        if isinstance(written, dict):
            return cls(**written)
        if not isinstance(written, list) or len(written) != 2 or not all(written):
            raise InvalidCriteria(
                f"a fold is written [function, field], e.g. ['sum', 'row_count']; got {written!r}"
            )
        return cls(function=written[0], field=written[1])


class Level(DataTransferObject):
    """One level of grouping: the field it splits by, and how finely.

    in      "produced_by"           →  Level(field='produced_by', grain=None)
    in      "registered_at:month"   →  Level(field='registered_at', grain='month')
    """

    field: str
    grain: str | None = None

    @classmethod
    def read(cls, written: Any) -> "Level":
        """A level as it comes in JSON: the object — `{"field": "registered_at", "grain": "month"}`.

        >>> Level.read({"field": "registered_at", "grain": "month"})
        Level(field='registered_at', grain='month')
        """
        if isinstance(written, Level):
            return written
        if isinstance(written, dict):
            return cls(**written)
        raise InvalidCriteria(
            f"a level is written {{'field': …, 'grain': …}}; got {written!r}"
        )


class Grouping(DataTransferObject):
    """How a result set is split, and how much of the split is answered at once.

        in      {"by": ["produced_by", "registered_at:month"], "totals": {"filas": ["sum", "row_count"]}}
        out     the first level, each bucket counted and folded, carrying how to open it

    `depth` is what keeps this honest: levels are answered one at a time by default, because
    a tree resolved whole is a cartesian product almost nobody expands. Opening a bucket is
    an ordinary search with `bucket.criteria`, which already holds the filter that got here.
    """

    by: tuple[Level, ...] = ()
    totals: dict[str, Fold] = {}
    depth: int = 1
    """How many levels to resolve now. Each level is one statement more and, at the bottom,
    every combination of what came before, so this is the number that makes the size of the
    answer visible at the call site."""

    @property
    def asked(self) -> bool:
        """Whether anything is being grouped at all.

        >>> Grouping().asked
        False
        """
        return bool(self.by)


class Bucket(DataTransferObject):
    """One group: what its rows share, how many there are, and how to see them.

        out     Bucket(field='produced_by', value=None, count=187,
                       totals={'filas': 4012933}, criteria=Criteria(where=…))

    `criteria` is the whole point. It is this reading plus «and this bucket's value», so
    opening the group is `self.repository.search(Dataset, bucket.criteria)` and nothing else — no client
    rebuilds a filter it did not write.
    """

    field: str
    value: Any
    count: int
    totals: dict[str, Any] = {}
    criteria: "Criteria"
    groups: list["Bucket"] = []
    """The level below, when `depth` reached it. Empty means «not resolved», not «none»: the
    difference is answered by opening the bucket."""


class Criteria(DataTransferObject):
    where: Expression | None = None
    """The filter: a `Condition`, or an `All`/`Any_`/`Not` grouping more of them.

    **The declared type is exactly what is accepted.** No abbreviated spelling and no
    conversion, so pydantic and the type checker see the same thing and `criteria.where` reads
    with an exhaustive `match`. All the validator does is build the nodes when the tree
    arrives serialised from JSON — which is the same tree, not another form."""

    order: tuple[Sort, ...] = ()

    pagination: Pagination = Field(default_factory=lambda: Pagination())
    """How many, and from where. Always the object: `{"limit": 80, "strategy": {"token": "eyJ…"}}`."""

    grouping: Grouping = Field(default_factory=lambda: Grouping())
    """How to split the result set, when it is being counted rather than listed. Ignored by a
    plain search: a page of records has no buckets."""

    count: CountMode = CountMode.CAPPED

    meta: bool = True
    """Whether the answer carries the model definition — what may be filtered, ordered and
    shown, and with which operators.

    **On by default, and off only when somebody says `meta=false`.** It was opt-in first, to
    save the kilobyte and a half on every page of a listing. What that bought in bytes it cost
    in surprise: a client asking a resource got an answer it could not build a form, a column
    menu or a filter from, and had to know to ask a second time for the part that explains the
    first. A door that describes itself is worth more than the kilobyte."""

    @field_validator("where", mode="before")
    @classmethod
    def _reads_the_filter(cls, value: Any) -> Any:
        """The filter in the one form there is — and in its serialisation, which is the same thing.

        1. A text is JSON: that is how a filter survives a URL.
        2. Final: anything else is already the tree, and gets built.

            in      '{"field": "row_count", "operator": ">", "value": 1000}'      what a URL carries
            out     Condition(row_count, 1000, GT)

        >>> Criteria(where={'field': 'row_count', 'operator': '>', 'value': 1000}).expression
        Condition(field='row_count', value=1000, operator=<Operator.GT: 'gt'>)
        """
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError as error:
                raise InvalidCriteria(f"'where' does not read as JSON: {error}") from error

        return expression_from(value)

    @field_validator("grouping", mode="before")
    @classmethod
    def _reads_the_grouping(cls, value: Any) -> Any:
        """The levels and the folds of the object, each to its type.

        in      {"by": [{"field": "produced_by"}], "totals": {"filas": ["sum", "row_count"]}}
        out     Grouping(by=(Level(produced_by),), totals={'filas': Fold(sum, row_count)})
        """
        if isinstance(value, dict):
            value = dict(value)
            if "by" in value:
                value["by"] = tuple(Level.read(one) for one in value["by"])
            if "totals" in value:
                value["totals"] = {
                    name: Fold.read(fold) for name, fold in (value["totals"] or {}).items()
                }
        return value

    @property
    def limit(self) -> int:
        """How many rows this page asks for.

        >>> Criteria(limit=80).limit
        80
        """
        return self.pagination.limit

    @property
    def cursor(self) -> str | None:
        """The token this page resumes from, when the strategy is one that resumes.

        >>> Criteria(cursor="eyJ").cursor
        'eyJ'
        >>> Criteria().cursor is None
        True
        """
        return self.pagination.cursor

    def resuming_from(self, token: str | None) -> "Criteria":
        """The same reading, continued from where a page ended.

            in      Criteria(limit=80), the token that came back with the page
            out     Criteria(limit=80, pagination=Pagination(limit=80, strategy=Cursor(token=…)))

        **This exists because `model_copy(update={"cursor": …})` does nothing.** `cursor` reads
        through to the strategy, so writing it past validation sets a field nobody reads: the
        page never advances and a loop that walks pages never ends. Which is exactly what
        happened, and what the tests caught.

        >>> self.repository.search(Dataset, criteria.resuming_from(page.cursor))
        """
        return self.model_copy(
            update={
                "pagination": self.pagination.model_copy(
                    update={"strategy": Cursor(token=token)}
                )
            }
        )

    @property
    def expression(self) -> Expression | None:
        """The filter. The name internals read it by, because `where` is what a caller writes.

        >>> Criteria(where={'field': 'row_count', 'operator': '>', 'value': 1000}).expression
        Condition(field='row_count', value=1000, operator=<Operator.GT: 'gt'>)
        >>> Criteria().expression is None
        True
        """
        return cast(Expression | None, self.where)

    def merged_with(self, other: "Criteria") -> "Criteria":
        """This criteria plus a more specific one.

            in      Criteria(where={'field': 'a', 'operator': '=', 'value': 1}, limit=20) + Criteria(where={'field': 'b', 'operator': '=', 'value': 2})
            out     Criteria(where=All([a==1, b==2]), limit=20)

        The rule has to be total or two readings quietly disagree.


        Conditions accumulate, because both filters apply. Scalars are replaced, because
        "twenty per page" and "fifty per page" have no combination. Sets union.

        The cursor is replaced and never carried over: it belongs to one ordering over one
        filter, and a merge that changed either would page through a set that no longer exists.
        """
        return Criteria(
            where=combined(self.expression, other.expression),
            order=other.order or self.order,
            pagination=(
                other.pagination
                if other.pagination != Pagination()
                else self.pagination.model_copy(
                    update={"strategy": other.pagination.strategy}
                )
            ),
            grouping=other.grouping if other.grouping.asked else self.grouping,
            count=other.count,
            # AND, not OR: with the definition travelling by default, an OR would mean nobody
            # could ever turn it off — the more specific criteria would always be overruled by
            # the default of the one it refines.
            meta=self.meta and other.meta,
        )
