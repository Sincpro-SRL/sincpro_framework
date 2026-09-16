"""Translates a filter and an ordering into SQLAlchemy constructs.

Nothing here plans a query or builds a join — SQLAlchemy does that. Rewriting this one file is
what a different backend would cost. See `docs/persistence/reference.md`.
"""

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import (
    ColumnElement,
    DateTime,
    Select,
    Text,
    and_,
    cast,
    func,
    not_,
    or_,
    select,
    tuple_,
)
from sqlalchemy.sql.elements import UnaryExpression

from sincpro_framework.ddd.criteria import (
    All,
    Any_,
    Condition,
    Criteria,
    Expression,
    Level,
    Not,
    Operator,
    Sort,
    parse_order,
)
from sincpro_framework.ddd.exceptions import ContractViolation, InvalidCriteria
from sincpro_framework.ddd.model_meta import Meta
from sincpro_framework.ddd.pagination import Pagination


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


def _comparison(column: Any, operator: Operator, value: Any) -> ColumnElement[bool]:
    """One operator as the SQL comparison it means.

    in      Dataset.row_count, Operator.GT, 1000
    out     dataset.row_count > 1000

    in      Dataset.name, Operator.LIKE, "Labs"
    out     lower(dataset.name) LIKE '%labs%'      containment, case-insensitive

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
            return column.in_(value)
        case Operator.NOT_IN:
            return column.not_in(value)
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
            return func.lower(column).like(f"%{str(value).lower()}%")
        case Operator.CONTAINS:
            return _as_stored_text(column).like(f'%"{value}"%')
        case Operator.NOT_CONTAINS:
            return not_(_as_stored_text(column).like(f'%"{value}"%'))
        case _:
            raise InvalidCriteria(f"no SQL translation for operator '{operator}'")


def where_clause(entity: type, expression: Expression | None) -> ColumnElement[bool] | None:
    """A validated expression as a `WHERE` clause.

        in      All([Condition(row_count, GT, 1000), Condition(name, LIKE, "labs")])
        out     dataset.row_count > 1000 AND lower(dataset.name) LIKE '%labs%'

        in      None
        out     None                            no filter asked, no clause built

    Every leaf has already been checked against the model, so nothing here refuses.
    """
    match expression:
        case None:
            return None
        case Condition():
            return _comparison(
                getattr(entity, expression.field), expression.operator, expression.value
            )
        case All():
            parts = (where_clause(entity, part) for part in expression.all)
            return and_(*(one for one in parts if one is not None))
        case Any_():
            parts = (where_clause(entity, part) for part in expression.any)
            return or_(*(one for one in parts if one is not None))
        case Not():
            inner = where_clause(entity, expression.negate)
            return not_(inner) if inner is not None else None


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
        getattr(entity, sort.field).desc() if sort.descending else getattr(entity, sort.field)
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

    columns = [getattr(model, sort.field) for sort in sorts]
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


GRAINS: tuple[str, ...] = ("day", "month", "week", "year")

# How a date is cut to a grain, per engine. One of the few pieces that change with the
# database — SQLite has `strftime`, Postgres `to_char` — so it is a registry the provider
# extends and not a table the framework closes. Every translator answers the same text for
# the same grain, which is what lets `bucket_range` read a bucket back without knowing who
# produced it.
GrainTranslator = Callable[[Any, str], ColumnElement[Any]]

# ISO weeks on both dialects, so a bucket reads the same text whatever the engine: `%G` and
# `%V` need SQLite 3.44 or later, which every supported Python ships.
_SQLITE_FORMATS = {"year": "%Y", "month": "%Y-%m", "day": "%Y-%m-%d", "week": "%G-W%V"}
_POSTGRESQL_FORMATS = {
    "year": "YYYY",
    "month": "YYYY-MM",
    "day": "YYYY-MM-DD",
    "week": 'IYYY-"W"IW',
}


def _sqlite_grain(column: Any, grain: str) -> ColumnElement[Any]:
    return func.strftime(_SQLITE_FORMATS[grain], column)


def _postgresql_grain(column: Any, grain: str) -> ColumnElement[Any]:
    """The `CAST` is what makes a timestamp stored as ISO text and a real `timestamp` group
    with the same expression."""
    return func.to_char(cast(column, DateTime), _POSTGRESQL_FORMATS[grain])


GRAIN_TRANSLATORS: dict[str, GrainTranslator] = {
    "sqlite": _sqlite_grain,
    "postgresql": _postgresql_grain,
}


def register_grain_translator(dialect: str, translator: GrainTranslator) -> None:
    """Teaches the translator how another engine cuts a date.

        register_grain_translator("mysql", lambda column, grain: func.date_format(column, …))

    The translator receives the column and one of `GRAINS`, and must answer the same text
    the built-in ones do for that grain — `2026-09` for a month — so buckets open the same
    way everywhere.
    """
    GRAIN_TRANSLATORS[dialect] = translator


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
    column = getattr(model, level.field)
    if level.grain is None:
        return column

    if level.grain not in GRAINS:
        raise InvalidCriteria(
            f"{level.grain!r} is not a granularity; use one of {sorted(GRAINS)}"
        )

    translator = GRAIN_TRANSLATORS.get(dialect)
    if translator is None:
        raise ContractViolation(
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
