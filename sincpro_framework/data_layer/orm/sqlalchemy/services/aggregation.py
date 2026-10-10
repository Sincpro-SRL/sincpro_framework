"""Grouping, folding and crossing rows in SQL: the statements behind `group_by_levels`, `measures`
and `pivot` — one per level or per axis, never one per bucket."""

import types
from collections.abc import Sequence
from typing import Any

from sqlalchemy import distinct, func, or_, select
from sqlalchemy.orm import Session

from sincpro_framework.data_layer.orm.sqlalchemy.services import sql_translator
from sincpro_framework.data_layer.orm.sqlalchemy.services.model_introspection import describe
from sincpro_framework.ddd.criteria import (
    Bucket,
    Criteria,
    Grouping,
    Level,
    Measure,
    PivotCell,
    conditions_of,
)
from sincpro_framework.ddd.entity.entity_meta import Meta
from sincpro_framework.ddd.exceptions import InvalidCriteria

WITHOUT_PERCENTILES = frozenset({"sqlite"})
"""The dialects with no `percentile_cont`. Named here so the refusal happens before a
statement exists, and says which engine could not."""

COUNT = "count"
"""What a grouping's `where_measures` and `order` call the number of rows in a group."""


def level_of(one: str | Level) -> Level:
    """A level as a caller writes it.

    in  "posted_at:month"  →  out  Level(field='posted_at', grain='month')
    in  "journal_id"       →  out  Level(field='journal_id')
    """
    if isinstance(one, Level):
        return one
    field, _, grain = one.partition(":")
    return Level(field=field, grain=grain or None)


def measure_columns(
    model: type, meta: Meta, measures: dict[str, Any], dialect: str = ""
) -> list[Any]:
    """`{'debit': ('sum', 'debit')}` as labelled aggregate columns, each name checked first.

    What a measure IS — which functions exist, how one is written — is `Measure.read` and
    nowhere else, so there is one answer for a criteria, for a pivot and for this.

    Two of them do not compile to `func.<name>(column)`:

    - `count_distinct` is `count(DISTINCT column)`, which every engine has;
    - `percentile` is `percentile_cont(f) WITHIN GROUP (ORDER BY column)`, which **SQLite does
      not have**. It is refused here, by name, rather than reaching the database and coming
      back as "no such function": a caller reading that message would look for the bug in the
      wrong place.
    """
    columns = []
    for name, written in measures.items():
        measure = Measure.read(written)
        meta.field(measure.field)
        column = getattr(model, measure.field)

        if measure.function == "count_distinct":
            columns.append(func.count(distinct(column)).label(name))
        elif measure.function == "percentile":
            if dialect in WITHOUT_PERCENTILES:
                raise InvalidCriteria(
                    f"'{dialect}' does not compute percentiles; ask for one over a database "
                    "that does, or answer it in memory"
                )
            columns.append(
                func.percentile_cont(measure.argument).within_group(column.asc()).label(name)
            )
        else:
            columns.append(getattr(func, measure.function)(column).label(name))
    return columns


def one_of(column: Any, values: list[Any]) -> Any:
    """`column IN (values)`, with NULL spelled out: `IN` never matches it."""
    present = [value for value in values if value is not None]
    clause = column.in_(present)
    return or_(clause, column.is_(None)) if len(present) != len(values) else clause


def bucketed(
    session: Session,
    model: type,
    criteria: Criteria,
    grouping: Grouping,
    clause: Any,
    dialect: str,
) -> list[Bucket]:
    """Every level the grouping names, one statement per level and never one per bucket.

        in      Dataset, Criteria(), by (produced_by, registered_at:month)
        level 1 SELECT produced_by, count(*), sum(row_count) GROUP BY produced_by
        level 2 SELECT produced_by, month, count(*), sum(…) GROUP BY produced_by, month
        out     [Bucket(produced_by, None, 187, {'filas': …}, criteria=…, groups=[…])]

    1. Validate every level's field once; build its grouping column for this dialect.
    2. Per level, group by the columns down to it: every bucket of that level, counted
       and folded exactly, in one statement, ordered so children arrive under parents.
    3. Per bucket, the criteria that opens it: the parent's AND this value, as a range
       when the level cuts a date, because the bucket's value is text and not a date.
    4. Final: nest bottom-up, so a bucket is built once with its children in hand.

    Two levels over eight buckets is two statements, not nine; four levels over a ledger
    is four. The size of the answer is the number of groups, which `by` decides.
    """
    meta = describe(model)
    resolved = grouping.group_by
    columns = []
    for level in resolved:
        meta.field(level.field)
        columns.append(sql_translator.grouping_column(model, level, dialect))
    counted = func.count().label(COUNT)
    # The same `measure_columns` the standalone folds and the pivot use, so a percentile is
    # refused here too instead of reaching the database as `percentile(size)`.
    folded = measure_columns(model, meta, dict(grouping.measures), dialect)
    measures = {name: column for name, column in zip(grouping.measures, folded)} | {
        COUNT: counted
    }
    where_measures = where_measures_clause(grouping, measures)

    rows_by_level: list[Sequence[Any]] = []
    kept_first: list[Any] | None = None
    for how_deep in range(1, len(resolved) + 1):
        keys = columns[:how_deep]
        statement = select(*keys, counted, *folded).select_from(model)
        if clause is not None:
            statement = statement.where(clause)
        if kept_first is not None:
            # The deeper levels answer for the groups the first level's page kept, and for
            # nothing else: a page of groups that dragged the whole tree behind it would
            # cost what the page was asked to save.
            statement = statement.where(one_of(columns[0], kept_first))
        statement = statement.group_by(*keys)
        if where_measures is not None:
            statement = statement.having(where_measures)
        statement = statement.order_by(*group_order(grouping, keys, measures))
        page = grouping.pagination
        if how_deep == 1 and page is not None:
            statement = statement.limit(page.limit).offset(page.skipped())
        rows = session.execute(statement).all()
        rows_by_level.append(rows)
        if how_deep == 1 and grouping.paged:
            kept_first = [row[0] for row in rows]

    opens: dict[tuple, Criteria] = {(): criteria}
    for how_deep, rows in enumerate(rows_by_level, start=1):
        level = resolved[how_deep - 1]
        for row in rows:
            path = tuple(row[:how_deep])
            # Opening a group is a plain reading of its rows: the grouping stays behind, or
            # a page asked on the bucket's criteria would be read as «a page per group».
            opens[path] = (
                opens[path[:-1]]
                .merged_with(Criteria(where=sql_translator.bucket_condition(level, path[-1])))
                .model_copy(update={"grouping": Grouping()})
            )

    pages = (
        ids_per_group(session, model, meta, criteria, clause, columns)
        if criteria.pagination.asked
        else {}
    )

    children: dict[tuple, list[Bucket]] = {}
    for how_deep in range(len(resolved), 0, -1):
        level = resolved[how_deep - 1]
        for row in rows_by_level[how_deep - 1]:
            path, count, measures = (
                tuple(row[:how_deep]),
                row[how_deep],
                row[how_deep + 1 :],
            )
            ids, next_cursor = pages.get(path, ([], None))
            children.setdefault(path[:-1], []).append(
                Bucket(
                    field=level.field,
                    value=path[-1],
                    count=count,
                    measures=dict(zip(grouping.measures, measures)),
                    criteria=opens[path],
                    ids=ids,
                    cursor=next_cursor,
                    groups=children.get(path, []),
                )
            )
    return children.get((), [])


def where_measures_clause(grouping: Grouping, measures: dict[str, Any]) -> Any:
    """`having` as a clause over what the groups folded, every name checked first.

        in      Condition(count, GT, 10), measures {'debit': sum(debit)}
        out     count(*) > 10

    The names it may use are the ones in `measures` and `count`; a name outside them is
    refused rather than dropped, because a filter that vanishes here would answer groups
    the caller ruled out.
    """
    if grouping.where_measures is None:
        return None
    for condition in conditions_of(grouping.where_measures):
        if condition.field not in measures:
            raise InvalidCriteria(
                f"'{condition.field}' is not something a group folded; `where_measures` reads "
                f"{', '.join(sorted(measures))} — a filter over the rows is `where`"
            )
    return sql_translator.clause_over(measures.__getitem__, grouping.where_measures)


def group_order(grouping: Grouping, keys: list[Any], measures: dict[str, Any]) -> list[Any]:
    """How the groups of a level come back: by its own columns, or by what they folded.

    in      nothing asked                    →  out  the group columns, ascending
    in      Sort(count, descending=True)     →  out  count(*) DESC
    in      Sort("debit", descending=True)   →  out  sum(debit) DESC
    """
    if not grouping.order:
        return list(keys)
    by_field = {level.field: column for level, column in zip(grouping.group_by, keys)}
    clauses = []
    for sort in grouping.order:
        # `or` would ask a column for its truth, which SQLAlchemy refuses on purpose.
        column = measures[sort.field] if sort.field in measures else by_field.get(sort.field)
        if column is None:
            raise InvalidCriteria(
                f"a grouping is ordered by a level, by a total or by count; "
                f"'{sort.field}' is none of those"
            )
        clauses.append(column.desc() if sort.descending else column.asc())
    return clauses


def ids_per_group(
    session: Session,
    model: type,
    meta: Meta,
    criteria: Criteria,
    clause: Any,
    columns: list[Any],
) -> dict[tuple, tuple[list[Any], str | None]]:
    """The first page of identities of every group of the deepest level, in one statement.

        SELECT id, <sort columns>, <group columns> FROM (
          SELECT …, row_number() OVER (PARTITION BY <group columns> ORDER BY <order>) AS position,
                    count(*)     OVER (PARTITION BY <group columns>)                 AS total
          FROM model WHERE <clause>
        ) WHERE position <= :limit

    Ids and not rows: the page is a reference the consumer opens with `browse` when it
    wants, and the specification never runs for rows nobody will look at. The cursor per
    group is minted from the sort values of its last id, so going on inside the group is an
    ordinary `search` with `bucket.criteria.resuming_from(bucket.cursor)`.
    """
    sorts = sql_translator.ordering_for(criteria, meta)
    order = sql_translator.order_clauses(model, sorts)
    keys = [
        column.label(f"{sql_translator.GROUP_KEY}{index}")
        for index, column in enumerate(columns)
    ]
    sort_columns = [getattr(model, sort.field) for sort in sorts]
    position = (
        func.row_number()
        .over(partition_by=columns, order_by=order)
        .label(sql_translator.POSITION)
    )
    total = func.count().over(partition_by=columns).label(sql_translator.TOTAL)
    inner = select(*sort_columns, *keys, position, total).select_from(model)
    if clause is not None:
        inner = inner.where(clause)
    sub = inner.subquery()
    statement = (
        select(sub)
        .where(sub.c[sql_translator.POSITION] <= criteria.limit)
        .order_by(*(sub.c[key.name] for key in keys), sub.c[sql_translator.POSITION])
    )

    pages: dict[tuple, tuple[list[Any], str | None]] = {}
    last: dict[tuple, tuple[Any, int]] = {}
    for row in session.execute(statement).all():
        path = tuple(row[len(sorts) + index] for index in range(len(keys)))
        ids, _ = pages.setdefault(path, ([], None))
        ids.append(getattr(row, meta.identity))
        last[path] = (row, row[-1])
    for path, (row, total_rows) in last.items():
        ids, _ = pages[path]
        position = types.SimpleNamespace(
            **{sort.field: getattr(row, sort.field) for sort in sorts}
        )
        cursor = (
            criteria.pagination.next_from(position, sorts) if total_rows > len(ids) else None
        )
        pages[path] = (ids, cursor)
    return pages


def pivot_cells(
    session: Session,
    model: type,
    clause: Any,
    row_columns: list[Any],
    column_columns: list[Any],
    folded: list[Any],
    measures: dict[str, str],
) -> list[PivotCell]:
    """One `GROUP BY` over whichever axes were handed in; no axis at all is the total."""
    keys = [*row_columns, *column_columns]
    statement = select(*keys, func.count(), *folded).select_from(model)
    if clause is not None:
        statement = statement.where(clause)
    if keys:
        statement = statement.group_by(*keys).order_by(*keys)
    return [
        PivotCell(
            row=list(row[: len(row_columns)]),
            column=list(row[len(row_columns) : len(keys)]),
            count=row[len(keys)],
            measures=dict(zip(measures, row[len(keys) + 1 :])),
        )
        for row in session.execute(statement).all()
    ]
