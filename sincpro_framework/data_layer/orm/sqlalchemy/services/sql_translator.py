"""Translates a filter and an ordering into SQLAlchemy constructs.

Nothing here plans a query or builds a join — SQLAlchemy does that. Rewriting this one file is
what a different backend would cost. See `docs/persistence/reference.md`.
"""

from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy
from sqlalchemy import (
    Boolean,
    ColumnElement,
    DateTime,
    Integer,
    Select,
    String,
    Text,
    all_,
    and_,
    any_,
    bindparam,
    cast,
    func,
    not_,
    or_,
    select,
    true,
    tuple_,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import QueryableAttribute
from sqlalchemy.sql.elements import UnaryExpression

from sincpro_framework.data_layer.orm.sqlalchemy.services import extension_typing
from sincpro_framework.ddd.criteria import (
    All,
    Any_,
    Condition,
    Criteria,
    Expression,
    FromField,
    Grain,
    Level,
    Not,
    Operator,
    Pagination,
    Sort,
    parse_order,
)
from sincpro_framework.ddd.entity import extensions
from sincpro_framework.ddd.entity.entity_meta import Meta
from sincpro_framework.ddd.exceptions import InvalidCriteria
from sincpro_framework.exceptions import ProgrammingError


def _as_stored_text(column: Any) -> Any:
    """The column read as the raw text on disk, past whatever type decorator wraps it.

        in      Dataset.column_names        a JSON text column
        out     CAST(dataset.column_names AS TEXT)

    Context: a `JsonText` column runs every bind parameter through `json.dumps`, which
    would turn the pattern `%"edad"%` into `"%\\"edad\\"%"` and match nothing. Casting compares
    against the JSON as stored.

    The one non-portable spot in this module: membership in a JSON array is a substring match
    on SQLite and would be `@>` on Postgres `jsonb`.
    """
    return cast(column, Text)


def _is_empty_list(value: Any) -> bool:
    """Whether a filter is asking about an empty list column.

    in  []          →  out  True
    in  ["a"]       →  out  False
    in  0           →  out  False      a falsy scalar is not an empty list
    """
    return isinstance(value, list) and not value


LIKE_ESCAPE = "\\"


def _escaped(value: Any) -> str:
    """A value as `LIKE` reads it literally: its wildcards and the escape itself escaped.

    in      "50%"     →  out  "50\\%"      the text 50%, not «starts with 50»
    in      "a_b"     →  out  "a\\_b"      the text a_b, not a, any one character, b
    """
    text = str(value)
    for special in (LIKE_ESCAPE, "%", "_"):
        text = text.replace(special, LIKE_ESCAPE + special)
    return text


def _literal(value: Any) -> str:
    """`_escaped`, lowercased: what a case-insensitive `LIKE` compares against."""
    return _escaped(str(value).lower())


MANY_MEMBERS = 2000
"""From how many values a membership test stops being written one parameter per value. PostgreSQL
accepts 65,535 parameters in a statement, and a question such as «the invoices of these deliveries»,
answered by another context, can name far more than that."""


class _ManyMembers(ColumnElement[bool]):
    """`column IN (many values)`, written for the engine that will run it.

    Where there is an array type (PostgreSQL) the values travel as ONE parameter,
    `column = ANY(:values)`: no limit on how many, and a plan that does not grow with them.
    Anywhere else it is the ordinary `IN`, which is what every engine reads.
    """

    inherit_cache = False
    type = Boolean()

    def __init__(self, column: Any, values: Sequence[Any], negated: bool) -> None:
        self.column = column
        self.values = list(values)
        self.negated = negated


@compiles(_ManyMembers)
def _members_in_a_list(element: _ManyMembers, compiler: Any, **kw: Any) -> str:
    chosen = (
        element.column.not_in(element.values)
        if element.negated
        else element.column.in_(element.values)
    )
    return compiler.process(chosen, **kw)


@compiles(_ManyMembers, "postgresql")
def _members_in_an_array(element: _ManyMembers, compiler: Any, **kw: Any) -> str:
    values = bindparam(None, element.values, type_=postgresql.ARRAY(element.column.type))
    chosen = (
        element.column != all_(values) if element.negated else element.column == any_(values)
    )
    return compiler.process(chosen, **kw)


def _members(column: Any, values: Sequence[Any], negated: bool) -> ColumnElement[bool]:
    """`column IN values` (or `NOT IN`), as one parameter per value up to `MANY_MEMBERS` and as
    an array from there."""
    if len(values) < MANY_MEMBERS or any(one is None for one in values):
        return column.not_in(values) if negated else column.in_(values)
    return _ManyMembers(column, values, negated)


def _comparison(column: Any, operator: Operator, value: Any) -> ColumnElement[bool]:
    """One operator as the SQL comparison it means.

    in      Dataset.row_count, Operator.GT, 1000
    out     dataset.row_count > 1000

    in      Dataset.name, Operator.LIKE, "Labs"
    out     lower(dataset.name) LIKE '%labs%'      containment, case-insensitive

    in      Dataset.name, Operator.STARTS_WITH, "Labs"
    out     lower(dataset.name) LIKE 'labs%'       prefix, case-insensitive

    in      Dataset.column_names, Operator.CONTAINS, "edad"
    out     CAST(dataset.column_names AS TEXT) LIKE '%"edad"%'
    """
    match operator:
        case Operator.EQ:
            # A list column stores NULL for "never recorded" and `[]` for "recorded as
            # nothing". A reader that answers both as an empty list means a filter
            # that only matched one of them would contradict what the reader answers — in the
            # catalogue that meant «uploaded» finding 1 dataset out of 185.
            if _is_empty_list(value):
                return or_(_as_stored_text(column).is_(None), column == value)
            return column == value
        case Operator.NE:
            if _is_empty_list(value):
                return and_(_as_stored_text(column).is_not(None), column != value)
            return column != value
        case Operator.IN:
            return _members(column, value, negated=False)
        case Operator.NOT_IN:
            return _members(column, value, negated=True)
        case Operator.GT:
            return column > value
        case Operator.GTE:
            return column >= value
        case Operator.LT:
            return column < value
        case Operator.LTE:
            return column <= value
        case Operator.BETWEEN:
            # Inclusive at both ends, which is what "between the 1st and the 31st" means to a
            # person — and what `BETWEEN` already means in SQL.
            return column.between(value[0], value[1])
        case Operator.IS_NULL:
            return column.is_(None) if value else column.is_not(None)
        case Operator.LIKE:
            # Case-insensitive containment: what the interface has always meant by searching.
            return func.lower(column).like(f"%{_literal(value)}%", escape=LIKE_ESCAPE)
        case Operator.STARTS_WITH:
            return func.lower(column).like(f"{_literal(value)}%", escape=LIKE_ESCAPE)
        case Operator.CONTAINS:
            return _as_stored_text(column).like(f'%"{_escaped(value)}"%', escape=LIKE_ESCAPE)
        case Operator.NOT_CONTAINS:
            return not_(
                _as_stored_text(column).like(f'%"{_escaped(value)}"%', escape=LIKE_ESCAPE)
            )
        case _:
            raise InvalidCriteria(f"no SQL translation for operator '{operator}'")


def clause_over(
    resolve: Callable[[str], Any], expression: Expression | None
) -> ColumnElement[bool] | None:
    """An expression as a clause, with `resolve` saying what a field name is a column of.

    The tree is the same wherever it is applied; only the name lookup changes. `WHERE` resolves
    a name to a model column, `HAVING` to the labelled aggregate a group folded.
    """
    match expression:
        case None:
            return None
        case Condition():
            value = expression.value
            if isinstance(value, FromField):
                value = resolve(value.name)
            return _comparison(resolve(expression.field), expression.operator, value)
        case All():
            parts = (clause_over(resolve, part) for part in expression.all)
            return and_(*(one for one in parts if one is not None))
        case Any_():
            parts = (clause_over(resolve, part) for part in expression.any)
            return or_(*(one for one in parts if one is not None))
        case Not():
            # The complement of what the inner expression matches, as memory and the client
            # answer it. A bare `NOT` over a comparison with a NULL is NULL, which drops the row:
            # `not owner = 'ana'` would leave out the records with no owner.
            inner = clause_over(resolve, expression.negate)
            return not_(inner.is_(true())) if inner is not None else None


def stored_by_an_extension(entity: type, field: str) -> Any:
    """What a field an extension below `entity` adds is, as a column of a statement over it.

        in      Move, "route"     →  out  (SELECT route FROM move_delivery WHERE id = move.id)
        in      Move, "zone"      →  out  move.zone              the extension keeps it in Move's table

    A field the entity does not have, filtered, ordered or grouped by like one it does. A record
    with no row of the extension holds NULL there. Reading it through a correlated subquery is
    the price of not joining every extension in every statement; a report over millions of
    rows reads the extension's class instead.
    """
    owner = extensions.owner_of(entity, field)
    if owner is None:
        return None
    table, key = extension_typing.STORED_IN[owner.entity]
    if key is None:
        return table.c[field]
    root_table = sqlalchemy.inspect(owner.root).local_table
    root_key = list(root_table.primary_key.columns)[0]
    return (
        select(table.c[field]).where(key == root_key).correlate(root_table).scalar_subquery()
    )


def column_attribute(entity: type, field: str) -> Any:
    """The mapped attribute a field filters: itself, or the attribute its column is mapped to.

        in      Invoice, "total"           →  out  Invoice.total
        in      LedgerEvent, "name"        →  out  LedgerEvent.wire_name    the `name` column

    An event's `name` is a class attribute — its wire name — so the table's `name` column is
    mapped under another attribute; a criteria still names it `name`.
    """
    attribute = getattr(entity, field, None)
    if isinstance(attribute, QueryableAttribute):
        return attribute
    mapper = sqlalchemy.inspect(entity)
    column = mapper.local_table.c.get(field)
    if column is None:
        stored = stored_by_an_extension(entity, field)
        return attribute if stored is None else stored
    return getattr(entity, mapper.get_property_by_column(column).key)


def where_clause(entity: type, expression: Expression | None) -> ColumnElement[bool] | None:
    """A validated expression as a `WHERE` clause.

        in      All([Condition(row_count, GT, 1000), Condition(name, LIKE, "labs")])
        out     dataset.row_count > 1000 AND lower(dataset.name) LIKE '%labs%'

        in      None
        out     None                            no filter asked, no clause built

    Every leaf has already been checked against the model, so nothing here refuses.
    """
    return clause_over(lambda field: column_attribute(entity, field), expression)


def ordering_for(criteria: Criteria, meta: Meta) -> tuple[Sort, ...]:
    """The ordering a page will actually use, tiebreaker included.

        in      Criteria(order=(Sort(row_count, desc),)), Meta of Dataset
        check   row_count is orderable                   nullable ones are refused
        out     (Sort(row_count, desc), Sort(dataset_id, desc))

        in      Criteria()                               nothing asked
        out     (Sort(dataset_id, desc),)                the model's default order

    A keyset needs a total order or rows repeat across pages, so the identity is appended —
    in the leading sort's direction, so a composite index on `(column, identity)` stays usable.
    """
    sorts = criteria.order or parse_order(meta.default_order)
    for sort in sorts:
        meta.orderable(sort.field)

    if any(sort.field == meta.identity for sort in sorts):
        return sorts
    return sorts + (Sort(field=meta.identity, descending=sorts[0].descending),)


def order_clauses(entity: type, sorts: tuple[Sort, ...]) -> list[UnaryExpression]:
    """The sorts as SQLAlchemy ordering.

    in      (Sort(row_count, desc), Sort(dataset_id, desc))
    out     [dataset.row_count DESC, dataset.dataset_id DESC]
    """
    return [
        (
            column_attribute(entity, sort.field).desc()
            if sort.descending
            else column_attribute(entity, sort.field)
        )
        for sort in sorts
    ]


def resume_clause(
    model: type, pagination: Pagination, sorts: tuple[Sort, ...]
) -> ColumnElement[bool] | None:
    """Everything past where the previous page ended, or nothing for a first page.

        in      Pagination(strategy=Cursor(token=…)), (size, -thing_id)
        keys    CursorKeys(keys=(3, 'th_9'), ordering='size,-thing_id')
        uniform (size, thing_id) > (3, 'th_9')                    one row-value comparison
        mixed   size > 3 OR (size = 3 AND thing_id < 'th_9')      the disjunction

        in      Pagination()  or  Pagination(strategy=Offset(rows=160))
        out     None                                             nothing to resume past

    A uniform ordering compiles to one row-value comparison, which a planner reads as a
    range scan over the composite index. A mixed one has no single tuple comparison, so it
    expands to the lexicographic disjunction `a < :a OR (a = :a AND b > :b)` — same rows,
    leading column still indexed.
    """
    resumed = pagination.keys_for(sorts)
    if resumed is None:
        return None

    columns = [column_attribute(model, sort.field) for sort in sorts]
    directions = {sort.descending for sort in sorts}

    if len(directions) == 1:
        descending = directions.pop()
        left, right = tuple_(*columns), tuple_(*resumed.keys)
        return left < right if descending else left > right

    clauses = []
    for index, sort in enumerate(sorts):
        equal_so_far = [columns[before] == resumed.keys[before] for before in range(index)]
        past_it = (
            columns[index] < resumed.keys[index]
            if sort.descending
            else columns[index] > resumed.keys[index]
        )
        clauses.append(and_(*equal_so_far, past_it))
    return or_(*clauses)


def capped_count(entity: type, clause: ColumnElement[bool] | None, cap: int) -> Select:
    """A count that stops at a ceiling, so its cost does not grow with the table.

        in      Dataset, row_count > 1000, cap 10000
        out     SELECT count(*) FROM (SELECT 1 FROM dataset WHERE … LIMIT 10001)

    Counting `cap + 1` and not `cap` is what distinguishes "exactly at the ceiling" from "at
    least the ceiling" — without it a table holding exactly ten thousand reads as `10000+`.
    """
    inner = select(1).select_from(entity)
    if clause is not None:
        inner = inner.where(clause)
    return select(func.count()).select_from(inner.limit(cap + 1).subquery())


GRAINS: tuple[str, ...] = tuple(one.value for one in Grain)

# How a date is cut to a grain, per engine. One of the few pieces that change with the
# database — SQLite has `strftime`, Postgres `to_char` — so it is a registry the provider
# extends and not a table the framework closes. Every translator answers the same text for
# the same grain, which is what lets `bucket_range` read a bucket back without knowing who
# produced it.
# Labels for the window a page per group is cut with. Names no column could be called, so a
# table with a `position` or a `total` column never collides with them.
POSITION, TOTAL, GROUP_KEY = "_sincpro_position", "_sincpro_total", "_sincpro_group_"

GrainTranslator = Callable[[Any, str], ColumnElement[Any]]

# ISO weeks on every dialect, so a bucket reads the same text whatever the engine. DuckDB reads
# `%G` and `%V`; SQLite only does from 3.46 (May 2024), and a Python built on an older library
# answers NULL for them, so SQLite computes the week without those formats (`_sqlite_iso_week`).
_SQLITE_FORMATS = {"year": "%Y", "month": "%Y-%m", "day": "%Y-%m-%d", "week": "%G-W%V"}
_POSTGRESQL_FORMATS = {
    "year": "YYYY",
    "month": "YYYY-MM",
    "day": "YYYY-MM-DD",
    "week": 'IYYY-"W"IW',
    "quarter": 'YYYY-"Q"Q',
}


def _thursday_of(column: Any) -> ColumnElement[Any]:
    """The Thursday of the ISO week a date falls in: a week belongs to the year of its Thursday,
    and its number is how many sevens fit before it. `-3 days` then the next Thursday (or the same
    one) lands on it for every weekday, Sunday included."""
    return func.date(column, "-3 days", "weekday 4")


def _sqlite_iso_week(column: Any) -> ColumnElement[Any]:
    """The ISO week number, 1 to 53, from functions every SQLite has: the day of the year of the
    week's Thursday, in sevens."""
    return (cast(func.strftime("%j", _thursday_of(column)), Integer) - 1) // 7 + 1


def _sqlite_grain(column: Any, grain: str) -> ColumnElement[Any]:
    """SQLite has no quarter in `strftime`: it is the year, `-Q` and the month rounded up to three."""
    if grain == "quarter":
        month = cast(func.strftime("%m", column), Integer)
        return (
            func.strftime("%Y", column)
            .op("||")("-Q")
            .op("||")(cast((month + 2) // 3, String))
        )
    if grain == "week":
        thursday = _thursday_of(column)
        return (
            func.strftime("%Y", thursday)
            .op("||")("-W")
            .op("||")(func.printf("%02d", _sqlite_iso_week(column)))
        )
    return func.strftime(_SQLITE_FORMATS[grain], column)


def _duckdb_grain(column: Any, grain: str) -> ColumnElement[Any]:
    """DuckDB reads the same format strings SQLite does — and takes them in the other order:
    `strftime(column, format)` against SQLite's `strftime(format, column)`. Which is the whole
    reason this is a registry and not one function with an `if`.
    """
    if grain == "quarter":
        return (
            func.strftime(column, "%Y")
            .op("||")("-Q")
            .op("||")(cast(func.quarter(column), String))
        )
    return func.strftime(column, _SQLITE_FORMATS[grain])


def _postgresql_grain(column: Any, grain: str) -> ColumnElement[Any]:
    """The `CAST` is what makes a timestamp stored as ISO text and a real `timestamp` group
    with the same expression."""
    return func.to_char(cast(column, DateTime), _POSTGRESQL_FORMATS[grain])


PartTranslator = Callable[[Any, str], ColumnElement[Any]]

_SQLITE_PART_FORMATS = {
    "year": "%Y",
    "month": "%m",
    "day_of_month": "%d",
    "hour": "%H",
}
_POSTGRESQL_PARTS = {
    "year": "year",
    "quarter": "quarter",
    "month": "month",
    "iso_week": "week",
    "day_of_week": "isodow",
    "day_of_month": "day",
    "hour": "hour",
}


def _sqlite_part(column: Any, part: str) -> ColumnElement[Any]:
    """A number out of a date, the way `strftime` writes it, read back as an integer. SQLite has
    no quarter: it is the month rounded up to three."""
    if part == "quarter":
        return (cast(func.strftime("%m", column), Integer) + 2) // 3
    if part == "iso_week":
        return _sqlite_iso_week(column)
    if part == "day_of_week":
        # `%w` counts Sunday as 0; ISO counts Monday as 1. (`%u` is SQLite 3.46 or later.)
        return (cast(func.strftime("%w", column), Integer) + 6) % 7 + 1
    return cast(func.strftime(_SQLITE_PART_FORMATS[part], column), Integer)


def _postgresql_part(column: Any, part: str) -> ColumnElement[Any]:
    """`EXTRACT` answers a number; the `CAST` makes a timestamp stored as ISO text and a real
    `timestamp` extract the same way. Weekdays are ISO, Monday as 1."""
    return cast(func.extract(_POSTGRESQL_PARTS[part], cast(column, DateTime)), Integer)


def _duckdb_part(column: Any, part: str) -> ColumnElement[Any]:
    return cast(func.date_part(_POSTGRESQL_PARTS[part], column), Integer)


PART_TRANSLATORS: dict[str, PartTranslator] = {
    "sqlite": _sqlite_part,
    "postgresql": _postgresql_part,
    "duckdb": _duckdb_part,
}

GRAIN_TRANSLATORS: dict[str, GrainTranslator] = {
    "sqlite": _sqlite_grain,
    "postgresql": _postgresql_grain,
    "duckdb": _duckdb_grain,
}


def grouping_column(model: type, level: Level, dialect: str = "sqlite") -> ColumnElement[Any]:
    """The column a grouping splits by, read at the grain asked for.

        in      Dataset, Level(field='produced_by')
        out     dataset.produced_by                          as is

        in      Dataset, Level(field='registered_at', grain='month'), "sqlite"
        out     strftime('%Y-%m', dataset.registered_at)     one bucket per month

        in      …, "postgresql"
        out     to_char(CAST(registered_at AS TIMESTAMP), 'YYYY-MM')

    Without a grain, grouping a catalogue by date answers one bucket per row: a listing with
    extra steps. A dialect with no translator is refused, naming how to register one.
    """
    column = column_attribute(model, level.field)
    if level.part is not None:
        part_translator = PART_TRANSLATORS.get(dialect)
        if part_translator is None:
            raise ProgrammingError(
                f"no date part translator for dialect {dialect!r}; the built-in ones are "
                f"{sorted(PART_TRANSLATORS)} — add yours with register_part_translator()"
            )
        return part_translator(column, level.part.value)
    if level.grain is None:
        return column

    if level.grain not in GRAINS:
        raise InvalidCriteria(
            f"{level.grain!r} is not a granularity; use one of {sorted(GRAINS)}"
        )

    translator = GRAIN_TRANSLATORS.get(dialect)
    if translator is None:
        raise ProgrammingError(
            f"no date grain translator for dialect {dialect!r}; the built-in ones are "
            f"{sorted(GRAIN_TRANSLATORS)} — add yours with register_grain_translator()"
        )
    return translator(column, level.grain)


def bucket_range(grain: str, value: str) -> tuple[datetime, datetime]:
    """Where a date bucket starts and ends, half open.

        in   "year",  "2026"        →  out  (2026-01-01, 2027-01-01)
        in   "month", "2026-09"     →  out  (2026-09-01, 2026-10-01)
        in   "day",   "2026-09-13"  →  out  (2026-09-13, 2026-09-14)
        in   "week",  "2026-W37"    →  out  (2026-09-14, 2026-09-21)
        in   "quarter", "2026-Q3"   →  out  (2026-07-01, 2026-10-01)

    **The end is OUT**, and that is not a detail: with an inclusive `between`, a row saved at
    00:00:00 on the first of the next month would fall in both buckets, and the counts of the
    levels would stop adding up to the parent's.
    """
    if grain == "year":
        start = datetime(int(value), 1, 1)
        return start, datetime(start.year + 1, 1, 1)
    if grain == "month":
        year, month = (int(part) for part in value.split("-"))
        start = datetime(year, month, 1)
        return start, datetime(year + (1 if month == 12 else 0), (month % 12) + 1, 1)
    if grain == "quarter":
        year, quarter = (int(part) for part in value.split("-Q"))
        start = datetime(year, 3 * (quarter - 1) + 1, 1)
        return start, datetime(year + (1 if quarter == 4 else 0), (3 * quarter) % 12 + 1, 1)
    if grain == "week":
        # ISO: "2026-W01" is the week holding the year's first Thursday, Monday to Sunday.
        year, week = (int(part) for part in value.split("-W"))
        start = datetime.fromisocalendar(year, week, 1)
        return start, start + timedelta(days=7)
    if grain == "day":
        start = datetime.fromisoformat(value)
        return start, start + timedelta(days=1)

    raise InvalidCriteria(f"{grain!r} is not a granularity; use one of {sorted(GRAINS)}")


def bucket_condition(level: Level, value: Any) -> Expression:
    """The condition that opens a bucket: what to ask to see its rows.

        in   Level(field="encoding"), "utf-8"
        out  Condition(encoding = 'utf-8')

        in   Level(field="registered_at", grain="year"), "2026"
        out  All([registered_at >= 2026-01-01, registered_at < 2027-01-01])

    **Without a grain it is an equality; with a grain it CANNOT be.** The value of a cut
    bucket is what `strftime` answered — the text "2026" — and asking a date column whether it
    equals "2026" cannot be answered: the model drops it as an unreadable value, the condition
    vanishes, and opening the bucket returns the whole catalogue as if the cut never existed.
    """
    if level.grain is None:
        return Condition(field=level.field, operator=Operator.EQ, value=value)

    start, end = bucket_range(level.grain, str(value))
    # In ISO and not as `datetime`: the bucket's criteria travels to the client as JSON, and
    # a `datetime` is not a JSON value. The model reads it back into a date when it accepts
    # it, the same path any filter written by hand in a URL takes.
    return All(
        all=[
            Condition(field=level.field, operator=Operator.GTE, value=start.isoformat()),
            Condition(field=level.field, operator=Operator.LT, value=end.isoformat()),
        ]
    )
