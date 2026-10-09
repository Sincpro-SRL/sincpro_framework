"""Every reading the repository answers: a criteria to a page, a count, a grouping, a pivot, an
export, a short question — and the relations a specification names, once per page."""

import types
from collections.abc import Iterator, Sequence
from typing import Any, cast, overload

from sqlalchemy import Select, distinct, func, literal, or_, select
from sqlalchemy.orm import Session, aliased

from sincpro_framework.ddd.criteria import (
    Bucket,
    Condition,
    CountMode,
    Criteria,
    Grouping,
    Level,
    Measure,
    Operator,
    Pivot,
    PivotCell,
    Sort,
    Specification,
    combined,
    conditions_of,
    parse_order,
)
from sincpro_framework.ddd.criteria.pagination import Pagination
from sincpro_framework.ddd.entity import ArchivableMixin, EventSourcedMixin
from sincpro_framework.ddd.entity.entity_collection import (
    Count,
    Dropped,
    EntityCollection,
    model_and_collection,
)
from sincpro_framework.ddd.entity.model_meta import Meta
from sincpro_framework.ddd.exceptions import (
    ContractViolation,
    InvalidCriteria,
)
from sincpro_framework.ddd.repositories.fingerprint import fingerprint_of
from sincpro_framework.orm.sqlalchemy.domain.registry import relations_of
from sincpro_framework.orm.sqlalchemy.services import sql_translator as sql
from sincpro_framework.orm.sqlalchemy.services.model_introspection import describe
from sincpro_framework.orm.sqlalchemy.services.relation_resolver import resolve_relations
from sincpro_framework.orm.sqlalchemy.services.workflows.store import (
    WITHOUT_PERCENTILES,
    Store,
)
from sincpro_framework.sincpro_abstractions import DataTransferObject

DEFAULT_COUNT_CAP = 10_000

Prepared = tuple[Any, tuple[Sort, ...], Meta, list[Dropped]]

COUNT = "count"
"""What a grouping's `where_measures` and `order` call the number of rows in a group."""


class Explained(DataTransferObject):
    """What a criteria will do, answered without doing it. See `Repository.explain`."""

    sql: str
    ordering: list[str] = []
    dropped: list[Dropped] = []
    relations: list[str] = []
    """The relation nodes that will be resolved, by path: `['author', 'author.books']`."""
    statements: int = 1
    """How many calls it will cost: the page, its count when one is asked, and one per
    relation node — whatever the number of rows."""


def _level(one: str | Level) -> Level:
    """A level as a caller writes it.

    in  "posted_at:month"  →  out  Level(field='posted_at', grain='month')
    in  "journal_id"       →  out  Level(field='journal_id')
    """
    if isinstance(one, Level):
        return one
    field, _, grain = one.partition(":")
    return Level(field=field, grain=grain or None)


def _measures(
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


def _one_of(column: Any, values: list[Any]) -> Any:
    """`column IN (values)`, with NULL spelled out: `IN` never matches it."""
    present = [value for value in values if value is not None]
    clause = column.in_(present)
    return or_(clause, column.is_(None)) if len(present) != len(values) else clause


def _relations_named(
    model: type, specification: Specification | None, path: str = ""
) -> list[str]:
    """Every relation a specification expands, by path, in the order it will be resolved.

        in      {"author": {"specification": {"books": {}}}, "tags": {}}
        out     ['author', 'author.books', 'tags']

    Read off what the data mapper declared and not off a definition: at this point nothing has
    been resolved, so the other side's definition does not exist yet.
    """
    if specification is None:
        return []
    declared = relations_of(model)
    named = []
    for name, node in specification.root.items():
        relation = declared.get(name)
        if relation is None:
            continue
        here = f"{path}{name}"
        named.append(here)
        named.extend(_relations_named(relation.related, node.specification, f"{here}."))
    return named


class Reading(Store):
    """Reads are generic because a filter is a filter: one `prepare` per criteria, one statement
    per page, relations resolved once for the whole page."""

    def prepare(self, model: type, criteria: Criteria) -> Prepared:
        """Everything a criteria becomes before any statement exists.

            in      Dataset, Criteria(where={'field': 'row_count', 'operator': '>', 'value': '1000'})
            read    describe(Dataset)                    fields, types, what is orderable
            accept  Condition(row_count, 1000, GT)       "1000" read as an integer
            out     (dataset.row_count > 1000, (Sort(dataset_id, desc),), Meta, [])
                     └ WHERE            └ ordering, tiebreaker included   └ Meta  └ dropped

        Kept together because `search`, `count` and `group_by` all need exactly these four, and
        a count computed from a different clause than the page it labels is silently wrong.
        """
        meta = describe(model)
        expression, dropped = meta.accept(self._asked(model, meta, criteria))
        _, unanswerable = meta.accept_specification(criteria.specification)
        clause = sql.where_clause(model, expression)
        return clause, sql.ordering_for(criteria, meta), meta, dropped + unanswerable

    def _counted(
        self, session: Session, model: type, clause: Any, criteria: Criteria, held: int | None
    ) -> Count | None:
        """How many matched, at the cheapest price that still answers honestly.

            held=12, limit=20, no cursor   →  Count(12, exact=True)     free, no query
            held=20, limit=20, 197 rows    →  Count(197, exact=True)    one capped count
            held=20, limit=20, 50k rows    →  Count(10000, exact=False) a floor, cost bounded
            count=NONE                     →  None

        `held` is how many rows the page brought back, or `None` when no page was fetched —
        reading "no page" as "zero rows" would answer `0` to every bare count.
        """
        if criteria.count is CountMode.NONE:
            return None

        first_page = criteria.cursor is None and criteria.pagination.skipped() == 0
        if held is not None and first_page and held < criteria.limit:
            return Count(value=held, exact=True)

        if criteria.count is CountMode.EXACT:
            statement = select(func.count()).select_from(model)
            if clause is not None:
                statement = statement.where(clause)
            return Count(value=session.scalar(statement) or 0, exact=True)

        seen = session.scalar(sql.capped_count(model, clause, DEFAULT_COUNT_CAP)) or 0
        return Count(value=min(seen, DEFAULT_COUNT_CAP), exact=seen <= DEFAULT_COUNT_CAP)

    def _page(
        self,
        session: Session,
        model: type,
        holder: type,
        statement: Select,
        criteria: Criteria,
        prepared: Prepared,
    ) -> EntityCollection:
        """Runs a prepared statement and wraps what comes back with honest metadata.

            limit   20
            fetch   21 rows                     one past the limit
            keep    20                          the extra one only says there is more
            out     EntityCollection(items=20, cursor=<of row 20>, count=Count(197, exact=True))

        Asking for one more row is how "is there a next page" is answered without a second
        query, and the last kept row is what the cursor is minted from.
        """
        clause, sorts, meta, dropped = prepared

        page = criteria.pagination
        skipped = page.skipped()
        if skipped:
            statement = statement.offset(skipped)
        rows = list(session.scalars(statement.limit(page.limit + 1)))
        kept = [self._read(row) for row in rows[: page.limit]]
        has_more = len(rows) > page.limit

        next_cursor = page.next_from(kept[-1], sorts) if has_more and kept else None

        if criteria.specification is not None and kept:
            dropped = list(dropped)
            meta = resolve_relations(
                self.prepare, session, model, kept, criteria.specification, meta, dropped
            )

        return holder(
            items=tuple(kept),
            cursor=next_cursor,
            count=self._counted(session, model, clause, criteria, len(kept)),
            dropped=tuple(dropped),
            meta=meta,
        )

    def _statement_from(self, model: type, criteria: Criteria, prepared: Prepared) -> Select:
        """The select a prepared criteria becomes.

        in      Dataset, a criteria at the cursor "eyJ…" paging by 20, its prepared parts
        out     SELECT dataset.* FROM dataset
                WHERE dataset.row_count > 1000 AND (dataset_id) < ('ds_01a0…')
                ORDER BY dataset.dataset_id DESC
        """
        clause, sorts, _, _ = prepared

        statement = select(model)
        if clause is not None:
            statement = statement.where(clause)

        # Where the page starts is the PAGINATION's answer, not a cursor's: which strategy is
        # in play is its business. With `Cursor` it is the "past that row" condition; with
        # `Offset` there is no condition and rows are skipped instead, in `_page`.
        resume = sql.resume_clause(model, criteria.pagination, sorts)
        if resume is not None:
            statement = statement.where(resume)

        return statement.order_by(*sql.order_clauses(model, sorts))

    def _bucketed(
        self, session: Session, model: type, criteria: Criteria, grouping: Grouping
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
        dialect = self.database.engine.dialect.name
        resolved = grouping.group_by
        columns = []
        for level in resolved:
            meta.field(level.field)
            columns.append(sql.grouping_column(model, level, dialect))
        counted = func.count().label(COUNT)
        # The same `_measures` the standalone folds and the pivot use, so a percentile is
        # refused here too instead of reaching the database as `percentile(size)`.
        folded = _measures(model, meta, dict(grouping.measures), dialect)
        measures = {name: column for name, column in zip(grouping.measures, folded)} | {
            COUNT: counted
        }
        where_measures = self._where_measures(grouping, measures)
        clause, _, _, _ = self.prepare(model, criteria)

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
                statement = statement.where(_one_of(columns[0], kept_first))
            statement = statement.group_by(*keys)
            if where_measures is not None:
                statement = statement.having(where_measures)
            statement = statement.order_by(*self._group_order(grouping, keys, measures))
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
                    .merged_with(Criteria(where=sql.bucket_condition(level, path[-1])))
                    .model_copy(update={"grouping": Grouping()})
                )

        pages = (
            self._ids_per_group(session, model, meta, criteria, clause, columns)
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

    def _where_measures(self, grouping: Grouping, measures: dict[str, Any]) -> Any:
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
        return sql.clause_over(measures.__getitem__, grouping.where_measures)

    def _group_order(
        self, grouping: Grouping, keys: list[Any], measures: dict[str, Any]
    ) -> list[Any]:
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
            column = (
                measures[sort.field] if sort.field in measures else by_field.get(sort.field)
            )
            if column is None:
                raise InvalidCriteria(
                    f"a grouping is ordered by a level, by a total or by count; "
                    f"'{sort.field}' is none of those"
                )
            clauses.append(column.desc() if sort.descending else column.asc())
        return clauses

    def _ids_per_group(
        self,
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
        sorts = sql.ordering_for(criteria, meta)
        order = sql.order_clauses(model, sorts)
        keys = [
            column.label(f"{sql.GROUP_KEY}{index}") for index, column in enumerate(columns)
        ]
        sort_columns = [getattr(model, sort.field) for sort in sorts]
        position = (
            func.row_number().over(partition_by=columns, order_by=order).label(sql.POSITION)
        )
        total = func.count().over(partition_by=columns).label(sql.TOTAL)
        inner = select(*sort_columns, *keys, position, total).select_from(model)
        if clause is not None:
            inner = inner.where(clause)
        sub = inner.subquery()
        statement = (
            select(sub)
            .where(sub.c[sql.POSITION] <= criteria.limit)
            .order_by(*(sub.c[key.name] for key in keys), sub.c[sql.POSITION])
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
                criteria.pagination.next_from(position, sorts)
                if total_rows > len(ids)
                else None
            )
            pages[path] = (ids, cursor)
        return pages

    def statement(self, target: type, criteria: Criteria) -> Select:
        """The escape hatch: this criteria as an ordinary `Select`, to take further.

            in      Dataset, Criteria(where={'field': 'row_count', 'operator': '>', 'value': 1000}, limit=20)
            out     SELECT dataset.* FROM dataset
                    WHERE dataset.row_count > 1000
                    ORDER BY dataset.dataset_id DESC

        The keyset is already applied when the criteria carries a cursor. Hand the result to
        `run` to get the usual envelope back.

        >>> stmt = self.repository.statement(Dataset, criteria).add_columns(func.row_number().over())
        >>> page = self.repository.run(Dataset, stmt, criteria)
        """
        model, _ = model_and_collection(target)
        return self._statement_from(model, criteria, self.prepare(model, criteria))

    @overload
    def run[C: EntityCollection](
        self, target: type[C], statement: Select, criteria: Criteria
    ) -> C: ...

    @overload
    def run[T](
        self, target: type[T], statement: Select, criteria: Criteria
    ) -> EntityCollection[T]: ...

    def run(self, target: type, statement: Select, criteria: Criteria) -> Any:
        """A statement built elsewhere — through `statement()` — executed and wrapped.

            in      Dataset, <a Select>, the criteria it was built from
            out     EntityCollection(items=…, cursor=…, count=…, dropped=…)

        The criteria comes back because the count and the cursor are read from it, not from
        the statement.
        """
        model, holder = model_and_collection(target)
        prepared = self.prepare(model, criteria)
        with self._session() as session:
            return self._page(session, model, holder, statement, criteria, prepared)

    def fingerprint(self, target: type, criteria: Criteria | None = None) -> str:
        """Context: the scope this repository was narrowed by is part of the key, so two tenants
        never share one."""
        model, _ = model_and_collection(target)
        return fingerprint_of(model, criteria or Criteria(), self._scope)

    @overload
    def search[C: EntityCollection](
        self,
        target: type[C],
        criteria: Criteria | None = None,
        for_update: bool = False,
        skip_locked: bool = False,
        nowait: bool = False,
    ) -> C: ...

    @overload
    def search[T](
        self,
        target: type[T],
        criteria: Criteria | None = None,
        for_update: bool = False,
        skip_locked: bool = False,
        nowait: bool = False,
    ) -> EntityCollection[T]: ...

    def search(
        self,
        target: type,
        criteria: Criteria | None = None,
        for_update: bool = False,
        skip_locked: bool = False,
        nowait: bool = False,
    ) -> Any:
        """The page this criteria asks for, with its cursor, its count and what was dropped.

            in      Dataset, Criteria(where={'field': 'row_count', 'operator': '>', 'value': 1000}, limit=20)
            steps   prepare once  →  build the select  →  one session  →  page + count
            out     EntityCollection(20 records of 197, more)

        `for_update` locks the page's rows until the unit of work commits — a worker that
        takes a batch of pending items and marks them taken. See `get`.

        >>> self.repository.search(Dataset, Criteria(pagination=Pagination(limit=20), order=parse_order("-registered_at")))
        EntityCollection(20 records of 197, more)
        >>> self.repository.search(Dataset)                      # no criteria: first page of everything
        EntityCollection(50 records of 197, more)
        """
        criteria = criteria or Criteria()
        lock = self._locking(for_update, skip_locked, nowait)

        # Prepared once and carried: going through the public `statement()` and `run()` would
        # describe the model, coerce the values and prune the expression twice per search.
        model, holder = model_and_collection(target)
        prepared = self.prepare(model, criteria)
        if criteria.grouping.asked and criteria.pagination.asked:
            with self._session() as session:
                return self._searched(
                    target, self._partitioned_page(session, model, holder, criteria, prepared)
                )
        statement = self._statement_from(model, criteria, prepared)
        if lock is not None:
            statement = statement.with_for_update(**(lock if isinstance(lock, dict) else {}))
        with self._session() as session:
            return self._searched(
                target, self._page(session, model, holder, statement, criteria, prepared)
            )

    def _partitioned_page(
        self,
        session: Session,
        model: type,
        holder: type,
        criteria: Criteria,
        prepared: Prepared,
    ) -> EntityCollection:
        """`grouping` with a page: the first `limit` rows of EVERY group, not of the set.

            in      Run, Criteria(where=dataset_id in (…), order=-started_at,
                                  pagination=Pagination(limit=5),
                                  grouping=Grouping(group_by=(Level(field="dataset_id"),)))
            out     up to five runs per dataset, ordered inside each, one statement

        This is what the other side of a relation resolved through a bus receives, so that no
        parent starves the others of a shared page. The count is the number of rows returned,
        exact; there is no cursor, because a page per group continues inside each group.
        """
        clause, sorts, meta, dropped = prepared
        dialect = self.database.engine.dialect.name
        columns = [
            sql.grouping_column(model, level, dialect) for level in criteria.grouping.group_by
        ]
        for level in criteria.grouping.group_by:
            meta.field(level.field)
        keys = [
            column.label(f"{sql.GROUP_KEY}{index}") for index, column in enumerate(columns)
        ]
        position = (
            func.row_number()
            .over(partition_by=columns, order_by=sql.order_clauses(model, sorts))
            .label(sql.POSITION)
        )
        inner = select(model, *keys, position)
        if clause is not None:
            inner = inner.where(clause)
        sub = inner.subquery()
        alias = aliased(model, sub)
        statement = (
            select(alias)
            .where(sub.c[sql.POSITION] <= criteria.limit)
            .order_by(*(sub.c[key.name] for key in keys), sub.c[sql.POSITION])
        )
        rows = [self._read(row) for row in session.scalars(statement)]
        return holder(
            items=tuple(rows),
            cursor=None,
            count=Count(value=len(rows), exact=True),
            dropped=tuple(dropped),
            meta=meta,
        )

    @overload
    def fetch_all[C: EntityCollection](
        self, target: type[C], criteria: Criteria | None = None
    ) -> C: ...

    @overload
    def fetch_all[T](
        self, target: type[T], criteria: Criteria | None = None
    ) -> EntityCollection[T]: ...

    def fetch_all(self, target: type, criteria: Criteria | None = None) -> Any:
        """Every record the criteria matches, as one complete collection.

            in      Dataset, Criteria(where=…, pagination=Pagination(limit=500))
            walk    page after page, following the cursors
            out     EntityCollection(1 240 records of 1 240)    exact count, no cursor

        The way to hand a bounded set to the in-memory algebra: what comes back is not a
        page, so `sum_by`, `grouped` and the set operations answer about everything. For a
        set that does not fit in memory, `stream` is the door — one page at a time.
        """
        pages = list(self.stream(target, criteria))
        _, holder = model_and_collection(target)
        items = tuple(record for page in pages for record in page.items)
        return holder(
            items=items,
            count=Count(value=len(items), exact=True),
            dropped=pages[0].dropped,
            meta=pages[0].meta,
        )

    @overload
    def stream[C: EntityCollection](
        self, target: type[C], criteria: Criteria | None = None
    ) -> Iterator[C]: ...

    @overload
    def stream[T](
        self, target: type[T], criteria: Criteria | None = None
    ) -> Iterator[EntityCollection[T]]: ...

    def stream(self, target: type, criteria: Criteria | None = None) -> Iterator[Any]:
        """Every page this criteria matches, one after the other, following the cursors.

            for page in self.repository.stream(Dataset, Criteria(pagination=Pagination(limit=500))):
                export(page)

        The way to walk a whole result set without holding it: each page is fetched when the
        loop reaches it, and a row inserted mid-walk neither repeats nor hides another —
        which is what the cursor buys. Under an `Offset` there is no cursor and the one page
        asked for is the whole walk.
        """
        criteria = criteria or Criteria()
        while True:
            page = self.search(target, criteria)
            yield page
            if page.cursor is None:
                return
            criteria = criteria.resuming_from(page.cursor)

    def _locking(self, for_update: bool, skip_locked: bool, nowait: bool) -> Any:
        """What `FOR UPDATE` becomes, or nothing.

        Refused outside a unit of work: a lock taken by a call that commits on its way out is
        released before the caller can act on it, which is a lock that protects nothing.
        `skip_locked` and `nowait` say what to do with a row somebody else holds — pass over it,
        or fail at once — so they cannot both be asked.
        """
        if not for_update:
            if skip_locked or nowait:
                raise ContractViolation(
                    "skip_locked and nowait say how to take a row lock; ask for one with "
                    "for_update=True"
                )
            return None
        if self._bound is None:
            raise ContractViolation(
                "for_update only means something inside context(): the lock is held "
                "until the block commits"
            )
        if skip_locked and nowait:
            raise ContractViolation(
                "skip_locked passes over a held row and nowait fails on it; ask for one"
            )
        if skip_locked:
            return {"skip_locked": True}
        if nowait:
            return {"nowait": True}
        return True

    def get[T](
        self,
        target: type[T],
        identity: Any,
        for_update: bool = False,
        skip_locked: bool = False,
        nowait: bool = False,
        detail: Criteria | None = None,
    ) -> T | None:
        """One record by its identity, or `None` when there is none.

            in      Dataset, "ds_01a0…"    →  out  Dataset(ds_01a0…, 'labs')
            in      Dataset, "gone"        →  out  None

        The read a use case does before it decides: what it gets back is the aggregate as
        stored, ready to be changed and handed to `save`. `detail` names the relations to
        resolve onto that one record (`detail_of(Model)`); the answer is still the record.

        `for_update` takes the row lock until the unit of work commits — for the process
        that claims an item so no other worker takes it. `skip_locked` steps over rows
        another worker already holds instead of waiting; `nowait` fails at once with
        `TransactionConflict`. SQLite has no row locks and ignores all three.
        """
        model, _ = model_and_collection(target)
        if isinstance(model, type) and issubclass(model, EventSourcedMixin):
            return cast(Any, self._rebuilt(model, identity))
        lock = self._locking(for_update, skip_locked, nowait)
        with self._session() as session:
            found = (
                session.get(model, identity)
                if lock is None
                else session.get(model, identity, with_for_update=lock)
            )
            if found is None or not self._in_scope(found):
                return None
            if isinstance(found, ArchivableMixin) and found.is_archived:
                return None
            read = self._read(found)
            if detail is not None and detail.specification is not None and read is not None:
                _, _, meta, dropped = self.prepare(model, detail)
                resolve_relations(
                    self.prepare,
                    session,
                    model,
                    [read],
                    detail.specification,
                    meta,
                    list(dropped),
                )
        return cast(Any, read)

    def _rebuilt(self, model: type[EventSourcedMixin], identity: Any) -> Any:
        """An event-sourced entity: its events, in the order of its versions, applied."""
        about = Criteria(
            where=combined(
                Condition(field="entity_type", value=model.__name__),
                Condition(field="entity_id", value=identity),
            ),
            order=parse_order("id"),
        )
        events = list(self.fetch_all(model.event_base, about).items)
        found = model.rebuilt(identity, events)
        return None if found is None else self._read(found)

    @overload
    def browse[C: EntityCollection](self, target: type[C], ids: Sequence[Any]) -> C: ...

    @overload
    def browse[T](self, target: type[T], ids: Sequence[Any]) -> EntityCollection[T]: ...

    def browse(self, target: type, ids: Sequence[Any]) -> Any:
        """Records by identity, answered in the order the ids were given.

            in      Dataset, ["ds_5", "ds_1", "gone"]
            fetch   one statement, WHERE dataset_id IN (…)
            out     EntityCollection([ds_5, ds_1])        ids that no longer exist are skipped

        Positional because a caller resolving a relation holds a list and needs it back in
        that order, not in whatever order the database felt like.
        """
        model, holder = model_and_collection(target)
        meta = describe(model)
        if not ids:
            return self._searched(target, holder(meta=meta))

        column = getattr(model, meta.identity)
        with self._session() as session:
            found = {
                getattr(record, meta.identity): self._read(record)
                for record in session.scalars(select(model).where(column.in_(list(ids))))
                if self._in_scope(record)
            }
        return self._searched(
            target, holder(items=tuple(found[one] for one in ids if one in found), meta=meta)
        )

    def count(self, target: type, criteria: Criteria | None = None) -> Count:
        """How many match, capped unless the criteria asked for the exact number.

            in      Dataset, Criteria(where={'field': 'row_count', 'operator': '>', 'value': 1000})
            out     Count(value=118, exact=True)

        No page is fetched here, so the free shortcut in `_counted` does not apply.
        """
        criteria = criteria or Criteria()
        model, _ = model_and_collection(target)
        clause, _, _, _ = self.prepare(model, criteria)
        with self._session() as session:
            return self._counted(session, model, clause, criteria, None) or Count(
                value=0, exact=True
            )

    def group_by(
        self, target: type, by: Sequence[str], criteria: Criteria | None = None
    ) -> list[dict[str, Any]]:
        """Counts per bucket over the whole result set — the question a facet asks.

            in      Dataset, ["produced_by"]
            out     SELECT produced_by, count(*) FROM dataset GROUP BY produced_by
                    [{'produced_by': None, 'count': 163}, {'produced_by': 'run_…', 'count': 1}]

        Rows are deliberately not returned: opening a bucket is an ordinary search with the
        bucket's values added to the criteria, so it pages inside the bucket. Not
        `EntityCollection.grouped`, which buckets a page.
        """
        if not by:
            raise InvalidCriteria(
                "group_by needs at least one field; grouping by nothing answers one bucket "
                "holding everything, which is a count and not a grouping"
            )

        criteria = criteria or Criteria()
        model, _ = model_and_collection(target)
        meta = describe(model)
        for field in by:
            meta.field(field)

        clause, _, _, _ = self.prepare(model, criteria)
        columns = [getattr(model, field) for field in by]
        statement = select(*columns, func.count().label("count")).select_from(model)
        if clause is not None:
            statement = statement.where(clause)

        with self._session() as session:
            rows = session.execute(statement.group_by(*columns).order_by(*columns)).all()
        return [dict(zip(by, row[:-1])) | {"count": row[-1]} for row in rows]

    def group_by_levels(self, target: type, criteria: Criteria | None = None) -> list[Bucket]:
        """The result set split by what its rows share — the question a facet asks.

            in      Dataset, Criteria(grouping={"by": ["produced_by"],
                                                "measures": {"filas": ["sum", "row_count"]}})
            out     [Bucket(produced_by, None, 187, {'filas': 4012933}, criteria=…),
                     Bucket(produced_by, 'run_01a0…', 1, {'filas': 400}, criteria=…)]

        Records are deliberately not returned: opening a bucket is an ordinary search with
        `bucket.criteria`, so it pages inside the bucket like any other reading.

        >>> for one in self.repository.group_by_levels(Dataset, Criteria(grouping=["produced_by"])):
        ...     page = self.repository.search(Dataset, one.criteria)      # that bucket's rows
        """
        criteria = criteria or Criteria()
        if not criteria.grouping.asked:
            raise InvalidCriteria(
                "grouping by nothing answers one bucket holding everything, which is a count "
                "and not a grouping; name at least one field in criteria.grouping"
            )

        model, _ = model_and_collection(target)
        with self._session() as session:
            return self._bucketed(session, model, criteria, criteria.grouping)

    def measures(
        self, target: type, criteria: Criteria | None = None, **measures: Any
    ) -> dict[str, Any]:
        """Folds over the whole result set, each named by the caller.

            in      Dataset, None, rows=("sum", "row_count"), biggest=("max", "size_bytes")
            build   SELECT sum(row_count) AS rows, max(size_bytes) AS biggest FROM dataset
            out     {'rows': 4012933, 'biggest': 151487508}

        A measure is `{function, field}` or the pair `("sum", "row_count")` — the same thing a
        criteria carries, read by the same `Measure.read` — and the names are the caller's, so a
        response reads as what was asked rather than `count_1`.

        This is where a `EntityCollection` sends anyone who tried to measure a page.
        """
        if not measures:
            raise InvalidCriteria(
                "measures needs at least one measure, e.g. rows=('sum', 'row_count')"
            )

        criteria = criteria or Criteria()
        model, _ = model_and_collection(target)
        meta = describe(model)
        clause, _, _, _ = self.prepare(model, criteria)

        statement = select(*_measures(model, meta, measures, self._dialect())).select_from(
            model
        )
        if clause is not None:
            statement = statement.where(clause)
        with self._session() as session:
            row = session.execute(statement).one()
        return dict(zip(measures, row))

    # ------------------------------------------------------------------ the short readings
    def exists(self, target: type, criteria: Criteria | None = None) -> bool:
        """Whether anything at all matches, without counting or fetching.

            in      Account, Criteria(where=Condition(field="code", value="1010"))
            out     True                       SELECT 1 … LIMIT 1

        The cheapest question there is: a guard before a write, a badge on a screen.
        """
        criteria = criteria or Criteria()
        model, _ = model_and_collection(target)
        prepared = self.prepare(model, criteria)
        clause = prepared[0]
        statement = select(literal(1)).select_from(model).limit(1)
        if clause is not None:
            statement = statement.where(clause)
        with self._session() as session:
            return session.scalar(statement) is not None

    @overload
    def first[T](
        self, target: type[EntityCollection[T]], criteria: Criteria | None = None
    ) -> T | None: ...

    @overload
    def first[T](self, target: type[T], criteria: Criteria | None = None) -> T | None: ...

    def first(self, target: type, criteria: Criteria | None = None) -> Any:
        """The first record the criteria's order puts in front, or `None`.

        >>> self.repository.first(Entries, Criteria(order=parse_order("-posted_at")))
        Entry(…)
        """
        criteria = criteria or Criteria()
        return self.search(
            target, criteria.model_copy(update={"count": CountMode.NONE, "meta": False})
        ).first()

    @overload
    def one[T](
        self, target: type[EntityCollection[T]], criteria: Criteria | None = None
    ) -> T: ...

    @overload
    def one[T](self, target: type[T], criteria: Criteria | None = None) -> T: ...

    def one(self, target: type, criteria: Criteria | None = None) -> Any:
        """The single record this criteria matches, refusing zero and refusing two.

            in      a criteria that matches exactly one   →  out  the record
            in      one that matches none, or several     →  ContractViolation

        Two rows are fetched and no more: what makes this safe is that it never loads a set to
        find out it was not one.
        """
        criteria = criteria or Criteria()
        return self.search(
            target,
            criteria.model_copy(
                update={
                    "pagination": Pagination(limit=2),
                    "count": CountMode.NONE,
                    "meta": False,
                }
            ),
        ).ensure_one()

    @overload
    def get_by[T](self, target: type[EntityCollection[T]], **values: Any) -> T | None: ...

    @overload
    def get_by[T](self, target: type[T], **values: Any) -> T | None: ...

    def get_by(self, target: type, **values: Any) -> Any:
        """One record by a natural key: the values that identify it besides its id.

            in      Account, code="1010"    →  out  Account(…) or None
            in      values two rows share   →  ContractViolation

        A natural key names one record; two answers mean it was not one, and guessing which
        would be the bug.
        """
        if not values:
            raise InvalidCriteria("get_by needs at least one value, e.g. code='1010'")
        asked = Criteria(
            where=combined(
                *(
                    Condition(field=field, operator=Operator.EQ, value=value)
                    for field, value in values.items()
                )
            ),
            pagination=Pagination(limit=2),
            count=CountMode.NONE,
            meta=False,
        )
        page = self.search(target, asked)
        if len(page) > 1:
            named = ", ".join(f"{field}={value!r}" for field, value in values.items())
            raise ContractViolation(
                f"{len(page)} records answer to {named}; a natural key names one"
            )
        return page.first()

    def pluck(self, target: type, field: str, criteria: Criteria | None = None) -> list[Any]:
        """One column of everything the criteria matches, without building a record.

            in      Line, "account_id", Criteria(where=…)
            out     ['01a0…', '01a0…', …]          SELECT account_id FROM line WHERE …

        Over the whole result set and not over a page, like `count` and `measures`: a column of
        values is what fills a select, seeds a `browse` or feeds an in-memory join, and a page
        of it would be an accident.
        """
        criteria = criteria or Criteria()
        model, _ = model_and_collection(target)
        clause, sorts, meta, _ = self.prepare(model, criteria)
        meta.field(field)
        statement = select(getattr(model, field)).select_from(model)
        if clause is not None:
            statement = statement.where(clause)
        with self._session() as session:
            return list(session.scalars(statement.order_by(*sql.order_clauses(model, sorts))))

    def distinct(
        self, target: type, field: str, criteria: Criteria | None = None
    ) -> list[Any]:
        """The values one column actually holds, each once, in order.

            in      Line, "entry_state"     →  out  ['draft', 'posted']

        What a filter's select offers, answered by the database rather than by a list somebody
        keeps in step with it by hand.
        """
        criteria = criteria or Criteria()
        model, _ = model_and_collection(target)
        clause, _, meta, _ = self.prepare(model, criteria)
        meta.field(field)
        column = getattr(model, field)
        statement = select(column).select_from(model).distinct().order_by(column)
        if clause is not None:
            statement = statement.where(clause)
        with self._session() as session:
            return list(session.scalars(statement))

    def export(
        self,
        target: type,
        criteria: Criteria | None = None,
        specification: Specification | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Every record the criteria matches, as dictionaries, one page at a time.

            for row in self.repository.export(Line, criteria, mask):
                writer.writerow(row)

        A walk and not a list: a report over a year of lines must not be held in memory to be
        written. Python values, not JSON — see `EntityCollection.to_records`.
        """
        criteria = criteria or Criteria()
        mask = specification if specification is not None else criteria.specification
        for page in self.stream(target, criteria):
            yield from page.to_records(mask)

    # ------------------------------------------------------------------ the whole picture
    def pivot(
        self,
        target: type,
        rows: Sequence[str | Level],
        columns: Sequence[str | Level],
        criteria: Criteria | None = None,
        **measures: Any,
    ) -> Pivot:
        """Groups crossed by groups: a table with folded cells and its margins.

            in      Line, rows=["journal_id"], columns=["posted_at:month"], debit=("sum", "debit")
            out     Pivot(rows=[['SAL'], …], columns=[['2026-01'], …], cells=[…],
                          row_margin=[…], column_margin=[…], total=PivotCell(count=75087, …))

        Four statements, whatever the size: the cells, the row margin, the column margin and
        the grand total. The margins are folded in the database and never added up from the
        cells, because an average of averages is not an average and a `min` of `min`s is only
        right by luck.

        >>> matrix = self.repository.pivot(Line, ["journal_id"], ["posted_at:month"], debit="sum:debit")
        >>> matrix.cell(["SAL"], ["2026-01"]).measures["debit"]
        Decimal('18240.50')
        """
        if not rows or not columns:
            raise InvalidCriteria(
                "a pivot needs a field on each axis; one axis alone is a grouping"
            )
        criteria = criteria or Criteria()
        model, _ = model_and_collection(target)
        clause, _, meta, _ = self.prepare(model, criteria)
        dialect = self.database.engine.dialect.name
        down = [_level(one) for one in rows]
        across = [_level(one) for one in columns]
        for level in [*down, *across]:
            meta.field(level.field)
        folded = _measures(model, meta, measures, self._dialect())
        row_columns = [sql.grouping_column(model, level, dialect) for level in down]
        column_columns = [sql.grouping_column(model, level, dialect) for level in across]

        with self._session() as session:
            cells = self._cells(
                session, model, clause, row_columns, column_columns, folded, measures
            )
            row_margin = self._cells(
                session, model, clause, row_columns, [], folded, measures
            )
            column_margin = self._cells(
                session, model, clause, [], column_columns, folded, measures
            )
            whole = self._cells(session, model, clause, [], [], folded, measures)

        return Pivot(
            rows=[one.row for one in row_margin],
            columns=[one.column for one in column_margin],
            cells=cells,
            row_margin=row_margin,
            column_margin=column_margin,
            total=whole[0] if whole else PivotCell(measures=dict.fromkeys(measures)),
        )

    def _cells(
        self,
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

    def explain(self, target: type, criteria: Criteria | None = None) -> "Explained":
        """What this criteria will do, without doing it.

            out     Explained(sql='SELECT … WHERE … ORDER BY …', ordering=['-posted_at', '-id'],
                              dropped=[Dropped(legacy_flag, unknown_field)],
                              relations=['author', 'author.books'], statements=4)

        For the three questions a criteria raises before it runs: what SQL it becomes, what of
        it the model refused, and how many calls it will cost. Nothing is executed.
        """
        criteria = criteria or Criteria()
        model, _ = model_and_collection(target)
        prepared = self.prepare(model, criteria)
        clause, sorts, meta, dropped = prepared
        statement = self._statement_from(model, criteria, prepared)
        relations = _relations_named(model, criteria.specification)
        counted = criteria.count is not CountMode.NONE
        return Explained(
            sql=str(statement.compile(self.database.engine)),
            ordering=[("-" if sort.descending else "") + sort.field for sort in sorts],
            dropped=list(dropped),
            relations=relations,
            statements=1 + (1 if counted else 0) + len(relations),
        )
