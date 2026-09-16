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

from collections.abc import Generator, Iterator, Sequence
from contextlib import contextmanager
from typing import Any, overload

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from sincpro_framework.ddd.criteria import (
    FOLD_FUNCTIONS,
    Bucket,
    CountMode,
    Criteria,
    Grouping,
    Sort,
)
from sincpro_framework.ddd.entity_collection import Count, Dropped, EntityCollection
from sincpro_framework.ddd.exceptions import (
    ContractViolation,
    DuplicateAggregate,
    InvalidCriteria,
    StaleAggregate,
)
from sincpro_framework.ddd.model_meta import Meta
from sincpro_framework.orm.sqlalchemy import sql_translator as sql
from sincpro_framework.orm.sqlalchemy.data_mapper import REPOSITORY
from sincpro_framework.orm.sqlalchemy.database import Database
from sincpro_framework.orm.sqlalchemy.model_introspection import describe
from sincpro_framework.orm.sqlalchemy.relation_resolver import resolve_relations

# Where counting stops unless somebody asks for more. Past this nobody is reading a total,
# they are refining a filter — Odoo settled on the same number for the same reason.
DEFAULT_COUNT_CAP = 10_000

# What a criteria becomes before any statement exists: the WHERE, the ordering, the model and
# whatever the caller asked for that this model cannot answer.
Prepared = tuple[Any, tuple[Sort, ...], Meta, list[Dropped]]


def model_and_collection(target: type) -> tuple[type, type]:
    """What was asked for, as the pair everything here works with.

        in      Dataset                 the mapped class
        out     (Dataset, EntityCollection)   the plain collection holds anything

        in      Datasets                class Datasets(EntityCollection[Dataset])
        out     (Dataset, Datasets)     the model read off the generic parameter

    A collection subclass is optional, so both spellings work.
    """
    if not isinstance(target, type) or not issubclass(target, EntityCollection):
        return target, EntityCollection

    held = target.holds()
    if held is None:
        raise ContractViolation(
            f"{target.__name__} does not say which aggregate it holds; "
            "declare it as EntityCollection[TheAggregate]"
        )
    return held, target


class Repository:
    """One database, read and written through one object. Implements `ddd.Repository` and
    answers more: a use case takes this instance, the protocol holds it to the minimum.

    Built once per bounded context and injected as `self.repository`. Every call opens its own
    session and commits it — except inside `context`, where the engine handed to the
    block shares one session and the block is the transaction.
    """

    def __init__(self, database: Database, session: Session | None = None) -> None:
        self.database = database
        self._bound = session

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
        expression, dropped = meta.accept(criteria.expression)
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
        """The levels `depth` asks for, one statement per level and never one per bucket.

            in      Dataset, Criteria(), by (produced_by, registered_at:month), depth 2
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
        is four. What `depth` makes visible is the size of the answer, not a query count.
        """
        meta = describe(model)
        dialect = self.database.engine.dialect.name
        resolved = grouping.by[: max(grouping.depth, 1)]
        columns = []
        for level in resolved:
            meta.field(level.field)
            columns.append(sql.grouping_column(model, level, dialect))
        folded = []
        for fold in grouping.totals.values():
            meta.field(fold.field)
            folded.append(getattr(func, fold.function)(getattr(model, fold.field)))
        clause, _, _, _ = self.prepare(model, criteria)

        rows_by_level: list[Sequence[Any]] = []
        for how_deep in range(1, len(resolved) + 1):
            keys = columns[:how_deep]
            statement = select(*keys, func.count(), *folded).select_from(model)
            if clause is not None:
                statement = statement.where(clause)
            rows_by_level.append(
                session.execute(statement.group_by(*keys).order_by(*keys)).all()
            )

        opens: dict[tuple, Criteria] = {(): criteria}
        for how_deep, rows in enumerate(rows_by_level, start=1):
            level = resolved[how_deep - 1]
            for row in rows:
                path = tuple(row[:how_deep])
                opens[path] = opens[path[:-1]].merged_with(
                    Criteria(where=sql.bucket_condition(level, path[-1]), grouping=Grouping())
                )

        children: dict[tuple, list[Bucket]] = {}
        for how_deep in range(len(resolved), 0, -1):
            level = resolved[how_deep - 1]
            for row in rows_by_level[how_deep - 1]:
                path, count, folds = tuple(row[:how_deep]), row[how_deep], row[how_deep + 1 :]
                children.setdefault(path[:-1], []).append(
                    Bucket(
                        field=level.field,
                        value=path[-1],
                        count=count,
                        totals=dict(zip(grouping.totals, folds)),
                        criteria=opens[path],
                        groups=children.get(path, []),
                    )
                )
        return children.get((), [])

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
        answers every method the outer one does. Nesting reuses the session in play.
        """
        if self._bound is not None:
            yield self
            return
        with self.database.session() as session:
            bound = Repository(self.database, session=session)
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
        statement = self._statement_from(model, criteria, prepared)
        if lock is not None:
            statement = statement.with_for_update(skip_locked=skip_locked)
        with self._session() as session:
            return self._page(session, model, holder, statement, criteria, prepared)

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
            if lock is None:
                return session.get(model, identity)
            return session.get(model, identity, with_for_update=lock)

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
        with self._session() as session:
            session.add(record)
            self._written(session, record)

    def remove(self, record: Any) -> None:
        """Deletes one aggregate that was read from this database.

            in      the Note that `get` returned   →  DELETE … WHERE id = :id

        Takes the record and not an id, so nothing is deleted that was not first loaded —
        and so a `version` check applies to a delete the way it does to an update.
        """
        with self._session() as session:
            session.delete(record)
            self._written(session, record)
