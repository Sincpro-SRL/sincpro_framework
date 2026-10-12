"""How a result set is split and counted: levels, measures, the groups and the pivot they make."""

from collections.abc import Sequence
from typing import Any

from pydantic import field_validator, model_validator

from sincpro_framework.ddd.criteria.domain.expression import Expression
from sincpro_framework.ddd.criteria.domain.pagination import Pagination
from sincpro_framework.ddd.criteria.domain.sort import Sort
from sincpro_framework.ddd.criteria.domain.strict import Strict, refused_or_dropped
from sincpro_framework.ddd.criteria.domain.values import Grain
from sincpro_framework.ddd.exceptions import InvalidCriteria
from sincpro_framework.sincpro_abstractions import DataTransferObject

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


class Measure(Strict):
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
    def read(cls, written: Any, tolerant: bool = False) -> "Measure":
        """A measure as it comes in: the object on the wire, or the pair written by hand.

        >>> Measure.read({"function": "sum", "field": "row_count"})
        Measure(function='sum', field='row_count')
        >>> Measure.read(["sum", "row_count"])
        Measure(function='sum', field='row_count')
        """
        if isinstance(written, Measure):
            return written
        if isinstance(written, dict):
            return cls(**refused_or_dropped(written, set(cls.model_fields), tolerant))
        # A tuple as much as a list: a pair written in Python is `("sum", "row_count")` and
        # the same pair read from JSON is a list. They mean the same measure.
        if not isinstance(written, (list, tuple)) or len(written) != 2 or not all(written):
            raise InvalidCriteria(
                "a measure is written {'function': …, 'field': …}, or as the pair "
                f"['sum', 'row_count']; got {written!r}"
            )
        return cls(function=written[0], field=written[1])


class Level(Strict):
    """One level of grouping: the field it splits by, and how finely.

    in      "produced_by"           →  Level(field='produced_by', grain=None)
    in      "registered_at:month"   →  Level(field='registered_at', grain='month')
    """

    field: str
    grain: Grain | None = None

    @field_validator("grain", mode="before")
    @classmethod
    def _a_granularity_that_exists(cls, grain: Any) -> Any:
        if grain is not None and grain not in {one.value for one in Grain}:
            raise InvalidCriteria(
                f"{grain!r} is not a granularity; use one of {sorted(one.value for one in Grain)}"
            )
        return grain

    @classmethod
    def read(cls, written: Any, tolerant: bool = False) -> "Level":
        """A level as it comes in JSON: the object — `{"field": "registered_at", "grain": "month"}`.

        >>> Level.read({"field": "registered_at", "grain": "month"})
        Level(field='registered_at', grain='month')
        """
        if isinstance(written, Level):
            return written
        if isinstance(written, dict):
            return cls(**refused_or_dropped(written, set(cls.model_fields), tolerant))
        raise InvalidCriteria(
            f"a level is written {{'field': …, 'grain': …}}; got {written!r}"
        )


class Grouping(Strict):
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


class Pivot(Strict):
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
