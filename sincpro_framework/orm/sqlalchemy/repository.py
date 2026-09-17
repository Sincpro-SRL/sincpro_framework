"""Runs a `Criteria` against a database and answers a `EntityCollection`; persists what a use case
built or changed.

The single door for reads and the single door for writes, so a concern added later — an access
mask, a cache, an audit — lands in one file. Reads are generic because a filter is a filter;
writes take the aggregate and nothing else, because a write that skips the aggregate skips its
rules. There is no update or delete by criteria.

    page = self.repository.search(Dataset, criteria)      a page, with cursor, count and definition
    one = self.repository.get(Dataset, "ds_01a0…")        by identity, or None
    self.repository.save(dataset)                         insert or update, version checked
    with self.repository.context() as repository:         several of those, one transaction
        run = repository.get(Run, run_id)
        run.advance()
        repository.save(run)
"""

import types
from collections.abc import Callable, Generator, Iterator, Sequence
from contextlib import contextmanager
from time import sleep
from typing import Any, cast, overload

from sqlalchemy import Select, func, literal, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased
from sqlalchemy.orm.exc import StaleDataError

from sincpro_framework.ddd.criteria import (
    FOLD_FUNCTIONS,
    Bucket,
    Condition,
    CountMode,
    Criteria,
    Grouping,
    Level,
    Operator,
    Pivot,
    PivotCell,
    Sort,
    Specification,
    combined,
    conditions_of,
)
from sincpro_framework.ddd.entity import Archivable
from sincpro_framework.ddd.entity_collection import (
    Count,
    Dropped,
    EntityCollection,
    model_and_collection,
)
from sincpro_framework.ddd.evaluate import matches
from sincpro_framework.ddd.exceptions import (
    ContractViolation,
    DuplicateAggregate,
    InvalidCriteria,
    StaleAggregate,
)
from sincpro_framework.ddd.model_meta import Meta
from sincpro_framework.ddd.pagination import Pagination
from sincpro_framework.orm.sqlalchemy import sql_translator as sql
from sincpro_framework.orm.sqlalchemy.data_mapper import REPOSITORY, relations_of
from sincpro_framework.orm.sqlalchemy.database import Database
from sincpro_framework.orm.sqlalchemy.model_introspection import describe
from sincpro_framework.orm.sqlalchemy.relation_resolver import resolve_relations
from sincpro_framework.sincpro_abstractions import DataTransferObject

# Where counting stops unless somebody asks for more. Past this nobody is reading a total,
# they are refining a filter — Odoo settled on the same number for the same reason.
DEFAULT_COUNT_CAP = 10_000

# What a criteria becomes before any statement exists: the WHERE, the ordering, the model and
# whatever the caller asked for that this model cannot answer.
Prepared = tuple[Any, tuple[Sort, ...], Meta, list[Dropped]]

COUNT = "count"
"""What a grouping's `having` and `order` call the number of rows in a group."""


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


def _folds(model: type, meta: Meta, folds: dict[str, str]) -> list[Any]:
    """`{'debit': 'sum:debit'}` as labelled aggregate columns, each name checked first."""
    columns = []
    for name, fold in folds.items():
        function, _, field = fold.partition(":")
        if not field:
            raise InvalidCriteria(
                f"'{name}={fold}' should read as 'function:field', e.g. 'sum:row_count'"
            )
        if function not in FOLD_FUNCTIONS:
            raise InvalidCriteria(
                f"'{function}' is not a fold; use one of {', '.join(FOLD_FUNCTIONS)}"
            )
        meta.field(field)
        columns.append(getattr(func, function)(getattr(model, field)).label(name))
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


class Repository:
    """One database, read and written through one object. Implements `ddd.Repository` and
    answers more: a use case takes this instance, the protocol holds it to the minimum.

    Built once per bounded context and injected as `self.repository`. Every call opens its own
    session and commits it — except inside `context`, where the engine handed to the
    block shares one session and the block is the transaction.
    """

    def __init__(
        self,
        database: Database,
        session: Session | None = None,
        scope: Criteria | None = None,
    ) -> None:
        self.database = database
        self._bound = session
        self._scope = scope

    def narrowed(self, scope: Criteria) -> "Repository":
        """The same database seen through a filter nothing can widen: what a tenant, a branch
        or a permission is.

            books = repository.narrowed(Criteria(where=Condition(field="tenant", value="acme")))
            books.search(Invoices, criteria)      →  … AND tenant = 'acme'
            books.save(invoice_of_another_tenant) →  ContractViolation

        Every reading merges the scope with AND, and every write is checked against it before
        it reaches the database, with the same evaluator the translator is specified by. An
        aggregate the scope cannot be expressed on is refused outright rather than read wide:
        a scope that is silently dropped is the one bug this exists to prevent.

        Narrowing again narrows further; the scopes accumulate, they never replace.
        """
        return Repository(
            self.database,
            session=self._bound,
            scope=self._scope.merged_with(scope) if self._scope is not None else scope,
        )

    def _asked(self, model: type, meta: Meta, criteria: Criteria) -> Any:
        """The filter that actually runs: what the caller asked, and what the repository adds.

        1. The scope, when this repository is narrowed, refused outright if this aggregate
           cannot answer it.
        2. Final: the archived left out, unless the caller named `archived_at` itself.
        """
        expression = criteria.expression
        if self._scope is not None:
            kept, dropped = meta.accept(self._scope.expression)
            if dropped or kept is None:
                raise ContractViolation(
                    f"{meta.aggregate} cannot answer the scope this repository was narrowed "
                    f"by ({', '.join(one.field for one in dropped) or 'nothing survived'}); "
                    "reading it wide is not an option"
                )
            expression = combined(kept, expression)
        if issubclass(model, Archivable) and not any(
            one.field == "archived_at" for one in conditions_of(expression)
        ):
            expression = combined(
                expression,
                Condition(field="archived_at", operator=Operator.IS_NULL, value=True),
            )
        return expression

    def _in_scope(self, record: Any) -> bool:
        """Whether a record belongs to what this repository may see and write."""
        return self._scope is None or matches(record, self._scope.expression)

    def _refuse_outside(self, record: Any) -> None:
        if not self._in_scope(record):
            raise ContractViolation(
                f"{type(record).__name__} lies outside what this repository was narrowed to; "
                "it can neither be written nor removed here"
            )

    @contextmanager
    def _session(self) -> Generator[Session]:
        """The session a call runs in: the bound one inside a unit of work, a fresh one
        otherwise. The fresh one commits when the call ends; the bound one commits when the
        unit of work does."""
        if self._bound is not None:
            yield self._bound
            return
        with self.database.session() as session:
            yield session

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
        kept = rows[: page.limit]
        has_more = len(rows) > page.limit

        next_cursor = page.next_from(kept[-1], sorts) if has_more and kept else None

        if criteria.specification is not None and kept:
            dropped = list(dropped)
            meta = resolve_relations(
                self, session, model, kept, criteria.specification, meta, dropped
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

        in      Dataset, Criteria(cursor="eyJ…", limit=20), its prepared parts
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
        resolved = grouping.by
        columns = []
        for level in resolved:
            meta.field(level.field)
            columns.append(sql.grouping_column(model, level, dialect))
        counted = func.count().label(COUNT)
        folded = []
        for name, fold in grouping.totals.items():
            meta.field(fold.field)
            folded.append(
                getattr(func, fold.function)(getattr(model, fold.field)).label(name)
            )
        folds = {name: column for name, column in zip(grouping.totals, folded)} | {
            COUNT: counted
        }
        having = self._having(grouping, folds)
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
            if having is not None:
                statement = statement.having(having)
            statement = statement.order_by(*self._group_order(grouping, keys, folds))
            if how_deep == 1 and grouping.paged:
                page = grouping.pagination
                assert page is not None
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
                path, count, folds = tuple(row[:how_deep]), row[how_deep], row[how_deep + 1 :]
                ids, next_cursor = pages.get(path, ([], None))
                children.setdefault(path[:-1], []).append(
                    Bucket(
                        field=level.field,
                        value=path[-1],
                        count=count,
                        totals=dict(zip(grouping.totals, folds)),
                        criteria=opens[path],
                        ids=ids,
                        cursor=next_cursor,
                        groups=children.get(path, []),
                    )
                )
        return children.get((), [])

    def _having(self, grouping: Grouping, folds: dict[str, Any]) -> Any:
        """`having` as a clause over what the groups folded, every name checked first.

            in      Condition(count, GT, 10), totals {'debit': sum(debit)}
            out     count(*) > 10

        The names it may use are the ones in `totals` and `count`; a name outside them is
        refused rather than dropped, because a filter that vanishes here would answer groups
        the caller ruled out.
        """
        if grouping.having is None:
            return None
        for condition in conditions_of(grouping.having):
            if condition.field not in folds:
                raise InvalidCriteria(
                    f"'{condition.field}' is not something a group folded; `having` reads "
                    f"{', '.join(sorted(folds))} — a filter over the rows is `where`"
                )
        return sql.clause_over(folds.__getitem__, grouping.having)

    def _group_order(
        self, grouping: Grouping, keys: list[Any], folds: dict[str, Any]
    ) -> list[Any]:
        """How the groups of a level come back: by its own columns, or by what they folded.

        in      nothing asked                    →  out  the group columns, ascending
        in      Sort(count, descending=True)     →  out  count(*) DESC
        in      Sort("debit", descending=True)   →  out  sum(debit) DESC
        """
        if not grouping.order:
            return list(keys)
        by_field = {level.field: column for level, column in zip(grouping.by, keys)}
        clauses = []
        for sort in grouping.order:
            # `or` would ask a column for its truth, which SQLAlchemy refuses on purpose.
            column = folds[sort.field] if sort.field in folds else by_field.get(sort.field)
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

    def _written(self, session: Session, record: Any) -> None:
        """Flushes one aggregate and translates the two ways a write loses a race.

        a newer version in the row     →  StaleAggregate     read again, decide again
        a unique value already taken   →  DuplicateAggregate  resolve the twin
        """
        try:
            session.flush()
        except StaleDataError as error:
            raise StaleAggregate(
                f"{type(record).__name__} changed since it was read; "
                "read it again before writing"
            ) from error
        except IntegrityError as error:
            raise DuplicateAggregate(
                f"{type(record).__name__} collides with a record already stored: "
                f"{error.orig}"
            ) from error

    @property
    def session(self) -> Session:
        """The session a unit of work runs in — the escape hatch for what the criteria will
        never say: a join across four tables, a window function, a bulk `UPDATE`, raw SQL.

            with self.repository.context() as repository:
                rows = repository.session.execute(select(Line.account_id, func.sum(Line.amount))…)

        Everything SQLAlchemy has, inside the same transaction, with the same stamping of
        `updated_at` and the same tracing. Only inside a unit of work: outside there is no
        session to hand over, and a caller that wanted one for a single statement wants
        `statement()` and `run()`.
        """
        if self._bound is None:
            raise ContractViolation(
                "the session exists only inside context(); open one, or use "
                "statement() and run() for a single read"
            )
        return self._bound

    def flush(self) -> None:
        """Writes what is pending to the database without committing — so a following read
        in the same unit of work sees it, and a constraint fails here rather than at the end.
        """
        self.session.flush()

    def commit(self) -> None:
        """Ends the current transaction and starts the next one, in the same unit of work.

            with self.repository.context() as repository:
                for batch in repository.stream(Line, criteria):
                    reconcile(batch)
                    repository.commit()                    each batch is durable on its own

        For a process that runs long: one transaction held across a thousand groups is a
        lock held for the whole run, and a failure at the end loses everything. The objects
        in hand stay usable — nothing expires on commit.
        """
        self.session.commit()

    @contextmanager
    def savepoint(self) -> Generator[None]:
        """A part of the unit of work that can fail on its own.

            with self.repository.context() as repository:
                for group in groups:
                    try:
                        with repository.savepoint():
                            reconcile(group)          raises → only this group is undone
                    except ReconciliationError:
                        report(group)

        What a savepoint is for: the groups that reconciled stay reconciled, the one that
        did not is rolled back to here, and the unit of work goes on.
        """
        with self.session.begin_nested():
            yield

    @contextmanager
    def context(self) -> Generator["Repository"]:
        """Several reads and writes as one transaction.

            with self.repository.context() as repository:
                run = repository.get(Run, run_id)          the same session
                run.advance()
                repository.save(run)                       flushed here, committed when the block ends
            an exception inside                     →  everything rolled back

        The engine handed to the block is this same engine bound to one session, so it
        answers every method the outer one does, and what it was narrowed to still holds
        inside. Nesting reuses the session in play.
        """
        if self._bound is not None:
            yield self
            return
        with self.database.session() as session:
            bound = Repository(self.database, session=session, scope=self._scope)
            # A relation touched inside the block resolves itself through this repository.
            session.info[REPOSITORY] = bound
            yield bound

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

    @overload
    def search[C: EntityCollection](
        self,
        target: type[C],
        criteria: Criteria | None = None,
        for_update: bool = False,
        skip_locked: bool = False,
    ) -> C: ...

    @overload
    def search[T](
        self,
        target: type[T],
        criteria: Criteria | None = None,
        for_update: bool = False,
        skip_locked: bool = False,
    ) -> EntityCollection[T]: ...

    def search(
        self,
        target: type,
        criteria: Criteria | None = None,
        for_update: bool = False,
        skip_locked: bool = False,
    ) -> Any:
        """The page this criteria asks for, with its cursor, its count and what was dropped.

            in      Dataset, Criteria(where={'field': 'row_count', 'operator': '>', 'value': 1000}, limit=20)
            steps   prepare once  →  build the select  →  one session  →  page + count
            out     EntityCollection(20 records of 197, more)

        `for_update` locks the page's rows until the unit of work commits — a worker that
        takes a batch of pending items and marks them taken. See `get`.

        >>> self.repository.search(Dataset, Criteria(limit=20, order=parse_order("-registered_at")))
        EntityCollection(20 records of 197, more)
        >>> self.repository.search(Dataset)                      # no criteria: first page of everything
        EntityCollection(50 records of 197, more)
        """
        criteria = criteria or Criteria()
        lock = self._locking(for_update, skip_locked)

        # Prepared once and carried: going through the public `statement()` and `run()` would
        # describe the model, coerce the values and prune the expression twice per search.
        model, holder = model_and_collection(target)
        prepared = self.prepare(model, criteria)
        if criteria.grouping.asked and criteria.pagination.asked:
            with self._session() as session:
                return self._partitioned_page(session, model, holder, criteria, prepared)
        statement = self._statement_from(model, criteria, prepared)
        if lock is not None:
            statement = statement.with_for_update(skip_locked=skip_locked)
        with self._session() as session:
            return self._page(session, model, holder, statement, criteria, prepared)

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
                                  grouping=Grouping(by=(Level(field="dataset_id"),)))
            out     up to five runs per dataset, ordered inside each, one statement

        This is what the other side of a relation resolved through a bus receives, so that no
        parent starves the others of a shared page. The count is the number of rows returned,
        exact; there is no cursor, because a page per group continues inside each group.
        """
        clause, sorts, meta, dropped = prepared
        dialect = self.database.engine.dialect.name
        columns = [
            sql.grouping_column(model, level, dialect) for level in criteria.grouping.by
        ]
        for level in criteria.grouping.by:
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
        rows = list(session.scalars(statement))
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

    def _locking(self, for_update: bool, skip_locked: bool) -> Any:
        """What `FOR UPDATE` becomes, or nothing.

        Refused outside a unit of work: a lock taken by a call that commits on its way out is
        released before the caller can act on it, which is a lock that protects nothing.
        """
        if not for_update:
            return None
        if self._bound is None:
            raise ContractViolation(
                "for_update only means something inside context(): the lock is held "
                "until the block commits"
            )
        return {"skip_locked": True} if skip_locked else True

    def get[T](
        self,
        target: type[T],
        identity: Any,
        for_update: bool = False,
        skip_locked: bool = False,
    ) -> T | None:
        """One record by its identity, or `None` when there is none.

            in      Dataset, "ds_01a0…"    →  out  Dataset(ds_01a0…, 'labs')
            in      Dataset, "gone"        →  out  None

        The read a use case does before it decides: what it gets back is the aggregate as
        stored, ready to be changed and handed to `save`.

        `for_update` takes the row lock until the unit of work commits — for the process
        that claims an item so no other worker takes it. `skip_locked` steps over rows
        another worker already holds instead of waiting. SQLite has neither and ignores both.
        """
        model, _ = model_and_collection(target)
        lock = self._locking(for_update, skip_locked)
        with self._session() as session:
            found = (
                session.get(model, identity)
                if lock is None
                else session.get(model, identity, with_for_update=lock)
            )
        if found is None or not self._in_scope(found):
            return None
        if isinstance(found, Archivable) and found.is_archived:
            return None
        return cast(Any, found)

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
            return holder(meta=meta)

        column = getattr(model, meta.identity)
        with self._session() as session:
            found = {
                getattr(record, meta.identity): record
                for record in session.scalars(select(model).where(column.in_(list(ids))))
                if self._in_scope(record)
            }
        return holder(items=tuple(found[one] for one in ids if one in found), meta=meta)

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
                                                "totals": {"filas": ["sum", "row_count"]}})
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

    def totals(
        self, target: type, criteria: Criteria | None = None, **folds: str
    ) -> dict[str, Any]:
        """Folds over the whole result set, each named by the caller.

            in      Dataset, None, rows="sum:row_count", biggest="max:size_bytes"
            build   SELECT sum(row_count) AS rows, max(size_bytes) AS biggest FROM dataset
            out     {'rows': 4012933, 'biggest': 151487508}

        Each fold reads `function:field` so the two halves never have to be guessed apart, and
        the names are the caller's so a response reads as what was asked rather than `count_1`.

        This is where a `EntityCollection` sends anyone who tried to fold a page.
        """
        if not folds:
            raise InvalidCriteria("totals needs at least one fold, e.g. rows='sum:row_count'")

        criteria = criteria or Criteria()
        model, _ = model_and_collection(target)
        meta = describe(model)
        clause, _, _, _ = self.prepare(model, criteria)

        columns = []
        for name, fold in folds.items():
            function, _, field = fold.partition(":")
            if not field:
                raise InvalidCriteria(
                    f"'{name}={fold}' should read as 'function:field', e.g. 'sum:row_count'"
                )
            if function not in FOLD_FUNCTIONS:
                raise InvalidCriteria(
                    f"'{function}' is not a fold; use one of {', '.join(FOLD_FUNCTIONS)}"
                )
            meta.field(field)
            columns.append(getattr(func, function)(getattr(model, field)).label(name))

        statement = select(*columns).select_from(model)
        if clause is not None:
            statement = statement.where(clause)
        with self._session() as session:
            row = session.execute(statement).one()
        return dict(zip(folds, row))

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

    def first(self, target: type, criteria: Criteria | None = None) -> Any:
        """The first record the criteria's order puts in front, or `None`.

        >>> self.repository.first(Entries, Criteria(order=parse_order("-posted_at")))
        Entry(…)
        """
        criteria = criteria or Criteria()
        return self.search(
            target, criteria.model_copy(update={"count": CountMode.NONE, "meta": False})
        ).first()

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

        Over the whole result set and not over a page, like `count` and `totals`: a column of
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
        **folds: str,
    ) -> Pivot:
        """Groups crossed by groups: a table with folded cells and its margins.

            in      Line, rows=["journal_id"], columns=["posted_at:month"], debit="sum:debit"
            out     Pivot(rows=[['SAL'], …], columns=[['2026-01'], …], cells=[…],
                          row_margin=[…], column_margin=[…], total=PivotCell(count=75087, …))

        Four statements, whatever the size: the cells, the row margin, the column margin and
        the grand total. The margins are folded in the database and never added up from the
        cells, because an average of averages is not an average and a `min` of `min`s is only
        right by luck.

        >>> matrix = self.repository.pivot(Line, ["journal_id"], ["posted_at:month"], debit="sum:debit")
        >>> matrix.cell(["SAL"], ["2026-01"]).totals["debit"]
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
        folded = _folds(model, meta, folds)
        row_columns = [sql.grouping_column(model, level, dialect) for level in down]
        column_columns = [sql.grouping_column(model, level, dialect) for level in across]

        with self._session() as session:
            cells = self._cells(
                session, model, clause, row_columns, column_columns, folded, folds
            )
            row_margin = self._cells(session, model, clause, row_columns, [], folded, folds)
            column_margin = self._cells(
                session, model, clause, [], column_columns, folded, folds
            )
            whole = self._cells(session, model, clause, [], [], folded, folds)

        return Pivot(
            rows=[one.row for one in row_margin],
            columns=[one.column for one in column_margin],
            cells=cells,
            row_margin=row_margin,
            column_margin=column_margin,
            total=whole[0] if whole else PivotCell(totals=dict.fromkeys(folds)),
        )

    def _cells(
        self,
        session: Session,
        model: type,
        clause: Any,
        row_columns: list[Any],
        column_columns: list[Any],
        folded: list[Any],
        folds: dict[str, str],
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
                totals=dict(zip(folds, row[len(keys) + 1 :])),
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

    # ------------------------------------------------------------------ writing

    def retrying[T](
        self,
        work: Callable[[], T],
        attempts: int = 3,
        on: type[Exception] | tuple[type[Exception], ...] = StaleAggregate,
        wait: float = 0.05,
    ) -> T:
        """Runs a unit of work again when it lost a race, and gives up saying so.

            def post() -> ResponsePostEntry:
                with self.repository.context() as ledger:
                    …
            answer = self.repository.retrying(post)

        A callable and not a block, because a `with` cannot run its body twice. The wait
        doubles between attempts so two workers that collided do not collide again on the
        same beat; the last failure is raised as it was, not wrapped.
        """
        if attempts < 1:
            raise ContractViolation("retrying needs at least one attempt")
        for attempt in range(attempts):
            try:
                return work()
            except on:
                if attempt == attempts - 1:
                    raise
                sleep(wait * (2**attempt))
        raise AssertionError("unreachable")

    def save_all(self, records: Sequence[Any]) -> None:
        """Persists many aggregates as one flush.

            in      1 000 lines, all new        →  one INSERT per batch, one round trip
            in      a mix of new and loaded     →  inserted and updated, versions checked
            in      one that moved on           →  StaleAggregate, nothing of the batch lands

        The same promises `save` makes, paid once instead of once per record: the version check
        holds for every one of them, and a batch that fails leaves the transaction to undo as a
        whole. This is the door an import or a nightly job uses.
        """
        if not records:
            return
        for record in records:
            self._refuse_outside(record)
        with self._session() as session:
            session.add_all(list(records))
            self._written(session, records[0])

    def remove_all(self, records: Sequence[Any]) -> None:
        """Removes many aggregates as one flush; archives the ones that can be archived."""
        if not records:
            return
        archivable = [one for one in records if isinstance(one, Archivable)]
        for record in archivable:
            record.archive()
        if archivable:
            self.save_all(archivable)
        rest = [one for one in records if not isinstance(one, Archivable)]
        if not rest:
            return
        for record in rest:
            self._refuse_outside(record)
        with self._session() as session:
            for record in rest:
                session.delete(record)
            self._written(session, rest[0])

    def purge(self, record: Any) -> None:
        """Deletes the row, archivable or not.

        `remove` archives what can be archived, which is what a business means by deleting.
        This is the other case: the record has to be gone, for a mistake or for a retention
        rule, and somebody said so.
        """
        self._refuse_outside(record)
        with self._session() as session:
            session.delete(record)
            self._written(session, record)

    def save(self, record: Any) -> None:
        """Persists one aggregate: an insert if it is new, an update if it was loaded.

            in      Note(title="x")                 never stored     →  INSERT, version 1
            in      the Note that `get` returned, changed           →  UPDATE … WHERE version = 1
            in      that same Note saved by somebody else first     →  StaleAggregate

        The aggregate is handed over whole and already valid: its rules ran before this call,
        and nothing here can change a field the aggregate did not. `updated_at` is stamped by
        the session and `version` raised by the mapping, so the caller does neither.

        Not a merge. A record built by hand with an id that already exists is a duplicate,
        not an update — the update path is to read the record and change it.
        """
        self._refuse_outside(record)
        with self._session() as session:
            session.add(record)
            self._written(session, record)

    def remove(self, record: Any) -> None:
        """Deletes one aggregate that was read from this database.

            in      the Note that `get` returned   →  DELETE … WHERE id = :id

        Takes the record and not an id, so nothing is deleted that was not first loaded —
        and so a `version` check applies to a delete the way it does to an update.

        **An `Archivable` aggregate is archived instead**, because that is what a business
        means by deleting one: it has to stop appearing and cannot be lost, since other
        records point at it. `purge` is the door for the other case.
        """
        self._refuse_outside(record)
        if isinstance(record, Archivable):
            record.archive()
            self.save(record)
            return
        with self._session() as session:
            session.delete(record)
            self._written(session, record)
