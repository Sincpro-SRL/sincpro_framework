"""What a caller sends: the filter, the ordering, the page.

One object and everything that goes inside it. A filter *is* a criteria — what the interface
calls a saved reading merges into this. See `docs/persistence/reference.md` and `specification.md`.

The filter language is deliberately small: conditions over one aggregate's own fields, combined
with and/or/not. No joins, no subqueries, no computed expressions — what this cannot say is
reached through the repository's escape hatch.
"""

import json
from collections.abc import Iterator, Sequence
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Union, cast

from pydantic import Field, JsonValue, RootModel, field_validator, model_validator

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


MEASURE_FUNCTIONS: tuple[str, ...] = (
    "sum",
    "avg",
    "min",
    "max",
    "count",
    "count_distinct",
    "percentile",
)
"""The five functions a measure may ask for. A closed list, because the name reaches SQL:
anything outside it is refused before a statement exists. Adding one is a change here, not a
string a caller can invent."""


class Measure(DataTransferObject):
    """One number a group answers: a function over a field, under a name the caller chose.

        in      {"function": "sum", "field": "row_count"}
        out     Measure(function='sum', field='row_count')       → sum(row_count) AS <name>

    **Named parts and not `"sum:row_count"`**, so nobody has to split a string in whatever
    language is reading this — the same reason a condition travels as `{field, operator,
    value}`. The pair `["sum", "row_count"]` is read as well, because writing one by hand in
    Python is a pair; what a caller sends over a wire is the object.

    It is called a measure and not an aggregate on purpose: in this codebase an *aggregate* is
    the DDD root — what `Meta.aggregate` names — and two meanings for one word in the same
    sentence is how a reader loses an afternoon.
    """

    function: str
    field: str

    argument: float | None = None
    """What the function needs besides a column, when it needs anything.

    Only `percentile` does: the fraction it cuts at, `0.5` for the median and `0.95` for a P95.
    Every other measure refuses one rather than ignoring it, because a number that is quietly
    dropped is a report that is quietly wrong."""

    @field_validator("function")
    @classmethod
    def _one_of_the_known_aggregates(cls, function: str) -> str:
        if function not in MEASURE_FUNCTIONS:
            raise InvalidCriteria(
                f"'{function}' is not a measure; use one of {', '.join(MEASURE_FUNCTIONS)}"
            )
        return function

    @model_validator(mode="after")
    def _the_argument_belongs_to_the_function(self) -> "Measure":
        """`percentile` needs its fraction; nothing else takes one.

        >>> Measure(function="percentile", field="debit", argument=0.95)
        Measure(function='percentile', field='debit', argument=0.95)
        >>> Measure(function="sum", field="debit", argument=0.95)
        InvalidCriteria: 'sum' takes no argument
        """
        if self.function == "percentile":
            if self.argument is None or not 0 < self.argument < 1:
                raise InvalidCriteria(
                    "a percentile is cut at a fraction between 0 and 1, e.g. 0.5 for the "
                    f"median; got {self.argument!r}"
                )
        elif self.argument is not None:
            raise InvalidCriteria(f"'{self.function}' takes no argument")
        return self

    @classmethod
    def read(cls, written: Any) -> "Measure":
        """A measure as it comes in: the object on the wire, or the pair written by hand.

        >>> Measure.read({"function": "sum", "field": "row_count"})
        Measure(function='sum', field='row_count')
        >>> Measure.read(["sum", "row_count"])
        Measure(function='sum', field='row_count')
        """
        if isinstance(written, Measure):
            return written
        if isinstance(written, dict):
            return cls(**written)
        # A tuple as much as a list: a pair written in Python is `("sum", "row_count")` and
        # the same pair read from JSON is a list. They mean the same measure.
        if not isinstance(written, (list, tuple)) or len(written) != 2 or not all(written):
            raise InvalidCriteria(
                "a measure is written {'function': …, 'field': …}, or as the pair "
                f"['sum', 'row_count']; got {written!r}"
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
    """How a result set is split: the levels, and the numbers folded out of every group.

        in      {"by": ["produced_by", "registered_at:month"], "measures": {"filas": ["sum", "row_count"]}}
        out     every level `by` names, each group counted and folded, carrying how to open it

    All the levels named are answered, one statement each: grouping by three fields is three
    statements whatever the number of rows. A screen that drills down one click at a time asks
    for one level and, on the click, reads `bucket.criteria` with a grouping of its own; it never
    has to know how deep the whole tree is.
    """

    group_by: tuple[Level, ...] = ()
    """The levels the set is split by, one statement each."""

    measures: dict[str, Measure] = {}
    """The numbers every group answers, each under a name the caller chose."""

    where_measures: Expression | None = None
    """A filter over what the groups measured, not over the rows: `count > 10`, `debit > 1000`.

    The names it may use are the ones in `measures` plus `count`. `where` filters the rows that
    make the groups; this filters the groups those rows made — which is why it is not called
    `where`: it reads measures, not fields."""

    order: tuple[Sort, ...] = ()
    """How the groups of each level come back: by the level's own field, by a name in `measures`
    or by `count`. Empty means by the level's field, ascending, which is how a list of groups
    reads when nobody asked for anything else. It orders the GROUPS; `Criteria.order` orders
    the rows, and with a page asked for it decides which rows each group shows first."""

    pagination: Pagination | None = None
    """How many groups of the FIRST level come back, and from where. `None`, the default, is
    every group: a grouping is a set of counts and a client asked for all of them. When a page
    is asked, the deeper levels answer only for the groups that survived it.

    A page and not a keyset: ordering by an aggregate has no stable key to resume from, so
    `Offset` is the honest strategy here and a cursor is not minted."""

    @property
    def paged(self) -> bool:
        """Whether only some of the groups were asked for.

        >>> Grouping(group_by=(Level(field="a"),)).paged
        False
        """
        return self.pagination is not None

    @property
    def asked(self) -> bool:
        """Whether anything is being grouped at all.

        >>> Grouping().asked
        False
        """
        return bool(self.group_by)


class Bucket(DataTransferObject):
    """One group: what its rows share, how many there are, and how to see them.

        out     Bucket(field='produced_by', value=None, count=187,
                       measures={'filas': 4012933}, criteria=Criteria(where=…))

    `criteria` is the whole point. It is this reading plus «and this bucket's value», so
    opening the group is `self.repository.search(Dataset, bucket.criteria)` and nothing else — no client
    rebuilds a filter it did not write.

    **A page of ids, when the criteria asked for a page.** `grouping` together with an explicit
    `pagination` means «a page per group»: every group of the deepest level answered carries the
    identities of its first `limit` rows in the criteria's order, an exact `count`, and a
    `cursor` to go on inside it. Ids and not records, the way Odoo's `search` answers before
    `browse` does: light enough for four thousand groups, stable enough to select, compare and
    open later.

        repository.browse(Line, bucket.ids)                            the first page, in order
        repository.search(Lines, bucket.criteria.resuming_from(bucket.cursor))   the rest
    """

    field: str
    value: Any
    count: int
    measures: dict[str, Any] = {}
    criteria: "Criteria"
    ids: list[Any] = []
    """The identities of this group's first page, in the criteria's order; empty unless the
    criteria asked for a page."""
    cursor: str | None = None
    """Where the next page inside this group starts; `None` when there is none, or none asked."""
    groups: list["Bucket"] = []
    """The level below, when the grouping named one. Empty on the deepest level."""


class PivotCell(DataTransferObject):
    """One cell of a pivot: what its rows share on both axes, how many there are, and the
    numbers folded out of them.

        out     PivotCell(row=['SAL'], column=['2026-01'], count=412, measures={'debit': …})

    A margin is a cell with one axis empty: `column=[]` is the whole row, `row=[]` the whole
    column, and both empty is the grand total.
    """

    row: list[Any] = []
    column: list[Any] = []
    count: int = 0
    measures: dict[str, Any] = {}


class Pivot(DataTransferObject):
    """A table of groups crossed by groups: rows down one axis, columns across the other, a
    folded cell where they meet.

        in      rows=[journal_id], columns=[posted_at:month], measures={'debit': sum debit}
        out     Pivot(rows=[['SAL'], ['PUR']], columns=[['2026-01'], ['2026-02']], cells=[…])

    `rows` and `columns` are every key that appeared, in order, so a client renders the grid
    without scanning the cells; `cells` holds only the crossings that have rows, because a
    pivot is mostly holes. The margins are folded in the database and not added up here: an
    average of averages is not an average.
    """

    rows: list[list[Any]] = []
    columns: list[list[Any]] = []
    cells: list[PivotCell] = []
    row_margin: list[PivotCell] = []
    """One cell per row key, folded over its whole row."""
    column_margin: list[PivotCell] = []
    """One cell per column key, folded over its whole column."""
    total: PivotCell = PivotCell()
    """Everything the criteria matched, folded once."""

    def cell(self, row: Sequence[Any], column: Sequence[Any]) -> PivotCell | None:
        """The cell where one row key crosses one column key, or `None` when it is empty.

        >>> pivot.cell(["SAL"], ["2026-01"]).count
        412
        """
        wanted = (list(row), list(column))
        for one in self.cells:
            if (one.row, one.column) == wanted:
                return one
        return None


class Specification(RootModel[dict[str, "Criteria"]]):
    """Field name → the criteria that says how to bring it.

        in      {"name": {}, "lines": {"pagination": {"limit": 20}}}
        out     Specification with two fields, the second carrying its own page

    **One type and no union**: every value is a `Criteria`, the same one as outside, so a
    consumer deserialises `dict[str, Criteria]` in any language and never has to tell a string
    from an object. A scalar's is empty, because there is nothing to say about how to bring a
    number. A relation's carries where, order, pagination and its own specification, so the
    vocabulary inside a node is the vocabulary outside it, at any depth.

    A `RootModel` and not an object with one field inside: in JSON it is the bare mapping,
    without a key that means nothing, and in here it has the methods that belong to it.
    """

    root: dict[str, "Criteria"] = {}

    def __iter__(self) -> Iterator[str]:  # type: ignore[override]
        return iter(self.root)

    def __contains__(self, name: str) -> bool:
        return name in self.root

    def __len__(self) -> int:
        return len(self.root)

    def __getitem__(self, name: str) -> "Criteria":
        return self.root[name]

    @property
    def named(self) -> list[str]:
        """The fields asked for, in the order they were asked.

        >>> Specification({"name": Criteria()}).named
        ['name']
        """
        return list(self.root)

    def narrowed_by(self, other: "Specification | None") -> "Specification":
        """This mask seen through another. **Intersection, never union.**

            in      {id, name, total} ∩ {id, name}      →  out  {id, name}
            in      {name} ∩ {total}                    →  out  {}   the identity alone

        A filter accumulates with AND because both restrictions hold; a mask intersects because
        a mask can only take away. That is what makes it safe as a permission: cutting twice can
        never show more than cutting once.
        """
        if other is None:
            return self
        return Specification(
            {
                name: criteria.merged_with(other[name])
                for name, criteria in self.root.items()
                if name in other
            }
        )


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

    specification: Specification | None = None
    """What to bring back of each record: field name → how to bring it, the value being a
    criteria exactly like this one. `None` and `{}` are NOT the same thing:

        None    nothing was asked          →  every scalar, no relation expanded
        {}      asked, nothing survived    →  the identity alone

    Read as one, a mask whose every field was dropped hands back the whole record, which is
    exactly what it existed to prevent."""

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
        Condition(field='row_count', value=1000, operator=<Operator.GT: '>'>)
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
        """The levels and the measures of the object, each to its type.

        in      {"by": [{"field": "produced_by"}], "measures": {"filas": ["sum", "row_count"]}}
        out     Grouping(by=(Level(produced_by),), measures={'filas': Measure(sum, row_count)})
        """
        if isinstance(value, dict):
            value = dict(value)
            if "group_by" in value:
                value["group_by"] = tuple(Level.read(one) for one in value["group_by"])
            if "measures" in value:
                value["measures"] = {
                    name: Measure.read(measure)
                    for name, measure in (value["measures"] or {}).items()
                }
            if "where_measures" in value:
                value["where_measures"] = expression_from(value["where_measures"])
            if isinstance(value.get("order"), str):
                value["order"] = parse_order(value["order"])
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
        Condition(field='row_count', value=1000, operator=<Operator.GT: '>'>)
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
        "twenty per page" and "fifty per page" have no combination. Sets union. The
        specification intersects, because a mask can only take away.

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
            specification=(
                other.specification
                if self.specification is None
                else self.specification.narrowed_by(other.specification)
            ),
            grouping=other.grouping if other.grouping.asked else self.grouping,
            count=other.count,
            # AND, not OR: with the definition travelling by default, an OR would mean nobody
            # could ever turn it off — the more specific criteria would always be overruled by
            # the default of the one it refines.
            meta=self.meta and other.meta,
        )


Specification.model_rebuild()
