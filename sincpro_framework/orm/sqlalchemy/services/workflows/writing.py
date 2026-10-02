"""Every write the repository makes: the aggregate whole through `save`, `remove` and `archive`,
and the doors named for what they skip — `upsert`, `update_all`, `remove_all`."""

from collections.abc import Mapping, Sequence
from typing import Any, cast

from sqlalchemy import Table, delete
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import update
from sqlalchemy.orm import Session
from sqlalchemy.schema import sort_tables

from sincpro_framework.ddd.criteria import (
    Criteria,
    conditions_of,
)
from sincpro_framework.ddd.entity import EventSourcedMixin, utc_now
from sincpro_framework.ddd.entity.entity_collection import (
    model_and_collection,
)
from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.ddd.exceptions import (
    ContractViolation,
    DuplicateAggregate,
    StaleAggregate,
)
from sincpro_framework.ddd.repositories.capabilities import Upserted
from sincpro_framework.ddd.repositories.repository import (
    records_of,
    refuse_unarchivable,
)
from sincpro_framework.orm.sqlalchemy.domain.transaction import Writes
from sincpro_framework.orm.sqlalchemy.infrastructure.engine_errors import named
from sincpro_framework.orm.sqlalchemy.infrastructure.flushing import only
from sincpro_framework.orm.sqlalchemy.services import sql_translator as sql
from sincpro_framework.orm.sqlalchemy.services import upsert as upsert_sql
from sincpro_framework.orm.sqlalchemy.services.cascade import (
    Plan,
    planned_removal,
    planned_save,
)
from sincpro_framework.orm.sqlalchemy.services.event_mapping import (
    is_mapped_event,
    map_new_event_classes,
)
from sincpro_framework.orm.sqlalchemy.services.model_introspection import describe
from sincpro_framework.orm.sqlalchemy.services.workflows.store import Store


def _named(records: list[Any]) -> str:
    """What a batch is called in an error: the aggregate when they are all one kind, and every
    kind in it when they are not. One flush writes them together and the engine does not say
    which one lost the race, so a single name would be a guess."""
    kinds = sorted({type(one).__name__ for one in records})
    if not kinds:
        return "the batch"
    if len(kinds) == 1:
        return kinds[0] if len(records) == 1 else f"one of {len(records)} {kinds[0]}"
    return f"one of {len(records)} records ({', '.join(kinds)})"


def _by_dependency(records: list[Any]) -> list[list[Any]]:
    """The records grouped by table, each table before the ones whose foreign keys point at it.

    Context: SQLAlchemy orders a flush by the relationships a mapping declares, and the
    framework's relations are its own, so without this one `save` of a zone and an address
    wrote them in class-name order — the address first, an orphan Postgres refuses. One table
    is one group: the common save is still one flush.
    """
    by_table: dict[Table, list[Any]] = {}
    for one in records:
        by_table.setdefault(sa_inspect(one).mapper.local_table, []).append(one)
    if len(by_table) == 1:
        return list(by_table.values())
    return [by_table[table] for table in sort_tables(by_table)]


class Writing(Store):
    """Writes take the aggregate, whole with the children it owns, because a write that skips the
    aggregate skips its rules; the two that skip it on purpose say so in their name."""

    def record_changes(self, record: Any) -> Any:
        """Flushes what is pending for this aggregate and hands back the fact that was written.

            with repository.context() as unit:
                invoice.post()
                event = unit.record_changes(invoice)     # settled here, and handed over

        **The same path the automatic mode takes**, asked for now rather than when the unit of
        work happens to end. Going around it with a second diff would record the change twice —
        once here and once at the flush — which is exactly what the one-event promise forbids.

        It closes one chapter: whatever moves after this call is a change of its own, with its
        own event, rather than being folded back into this one.
        """
        if self._bound is None:
            raise ContractViolation(
                "record_changes needs the unit of work holding this aggregate — outside "
                "context() there is nothing pending to write down, and a plain save() is "
                "what settles it"
            )
        seen = len(record.recorded_events()) if hasattr(record, "recorded_events") else 0
        self._written(self._bound, [record])
        after: tuple[Any, ...] = (
            record.recorded_events() if hasattr(record, "recorded_events") else ()
        )
        return after[-1] if len(after) > seen else None

    def _written(self, session: Session, records: list[Any]) -> None:
        """Flushes the batch and names what the engine refused (`engine_errors.py`).

        **Named for the whole batch, not for its first record.** One flush writes all of them
        and the engine does not say which one lost, so naming a guess sends somebody to read
        the wrong aggregate. The engine's own message does name the table; it is carried
        through rather than summarised away.
        """
        with named(_named(records)):
            if self._transaction.writes is Writes.SAVED and self._bound is not None:
                # Only what this save named: a record changed beside it is not written in passing.
                with only(session, records):
                    session.flush(objects=records)
            else:
                session.flush()

    def _carried_out(self, session: Session, plan: Plan) -> list[bool]:
        """Writes a plan in the one order every write needs, and answers what the before-moments
        worked out about each record.

        1. Every record is checked against the scope, and the before-moments run for all of them.
        2. The detached first: a child's key is NULL before its root can go.
        3. The removals, deepest first: an orphan leaves before the child taking its unique
           value arrives.
        4. Final: the rest, parents before children — a root whose parts move is updated with
           its version checked and raised; the assignments carried out are marked written.
        """
        stored, removed = plan.stored, plan.removed
        for one in [*stored, *removed]:
            self._refuse_outside(one)
        for one in removed:
            self._before_remove(one)
        newness = self._before_writes(stored)
        plan.version_moves()
        for batch in _by_dependency(plan.detached):
            session.add_all(batch)
            self._written(session, batch)
        for level in reversed(plan.removals):
            for one in level:
                session.delete(one)
            self._written(session, level)
        for batch in _by_dependency(plan.writes):
            session.add_all(batch)
            self._written(session, batch)
        plan.written()
        self._facts_kept(session, [*plan.stored, *plan.removed])
        return newness

    def _facts_kept(self, session: Session, records: list[Any]) -> None:
        """What the records of this write recorded, kept in their context's event table in the
        same session — after the flush, so the events recorded while flushing (change tracking's
        `EntityUpdated`) are among them. An event whose class has no event table stays in memory.

        **Read, never taken.** The aggregate keeps its events — `pull_events()` still hands them
        to whoever wants them. An event is kept once by its id, so saving the same aggregate
        again keeps only what is new.
        """
        events = [
            event
            for one in records
            if not isinstance(one, DomainEvent) and hasattr(one, "recorded_events")
            for event in one.recorded_events()
            if is_mapped_event(type(event))
        ]
        fresh = self._not_kept(session, events)
        if fresh:
            session.add_all(fresh)
            with named(_named(fresh)):
                session.flush(objects=fresh)

    def _not_kept(self, session: Session, events: list[Any]) -> list[Any]:
        """The events not kept yet, each once — by id, in the order given."""
        map_new_event_classes()
        fresh: list[Any] = []
        seen: set[str] = set()
        with session.no_autoflush:
            for event in events:
                if event.id in seen or session.get(type(event), event.id) is not None:
                    continue
                seen.add(event.id)
                fresh.append(event)
        return fresh

    def _events_of_sourced(self, session: Session, records: list[Any]) -> list[Any]:
        """The records to write, an event-sourced entity standing for the events it recorded
        since it was rebuilt — its state has no row; its events are its state."""
        written: list[Any] = []
        for one in records:
            if isinstance(one, EventSourcedMixin):
                written.extend(self._not_kept(session, one.unsaved_events()))
            else:
                written.append(one)
        return written

    def _announced(self, plan: Plan, newness: list[bool]) -> None:
        for one in plan.removed:
            self._after_remove(one)
        self._after_writes(plan.stored, newness)

    def save(self, record: Any) -> None:
        """Persists one aggregate or several: an insert for what is new, an update for what was
        loaded.

            in      Note(title="x")                 never stored     →  INSERT, version 1
            in      the Note that `get` returned, changed           →  UPDATE … WHERE version = 1
            in      that same Note saved by somebody else first     →  StaleAggregate
            in      1 000 new lines, or a page      →  one flush, one INSERT

        **One or several is the same call.** Several are written as one flush, with the same
        promises paid once instead of once per record: the version check holds for every one of
        them, and a batch that fails leaves the transaction to undo as a whole. This is the door
        an import or a nightly job uses too.

        **What a batch costs, measured.** A thousand new aggregates are one `INSERT` with a
        thousand rows. A thousand *loaded* ones are a thousand `UPDATE` statements, one per row,
        and that is the price of `StaleAggregate`: the engine has to read the affected row count
        back for each one to know whether somebody moved it first, which is exactly what a
        batched statement does not report per row. Worth knowing before writing the nightly job
        — the flush is one, the round trips are not.

        The aggregate is handed over whole and already valid: its rules ran before this call,
        and nothing here can change a field the aggregate did not. `updated_at` is stamped by
        the session and `version` raised by the mapping, so the caller does neither.

        Not a merge. A record built by hand with an id that already exists is a duplicate,
        not an update — the update path is to read the record and change it.
        """
        self._refuse_reentrant_write()
        self._refuse_read_only()
        records = records_of(record)
        if not records:
            return
        try:
            with self._session() as session:
                plan = planned_save(session, self._events_of_sourced(session, records))
                newness = self._carried_out(session, plan)
        except DuplicateAggregate as error:
            if "entity_version" in str(error):
                raise StaleAggregate(
                    f"{_named(records)} changed since it was rebuilt: another writer appended "
                    "its next event first; rebuild it and try again"
                ) from error
            raise
        self._announced(plan, newness)

    def remove(self, record: Any) -> None:
        """Deletes one aggregate or several, read from this database.

            in      the Note that `get` returned   →  DELETE … WHERE id = :id
            in      a list, or a page              →  one flush

        Takes records and not ids, so nothing is deleted that was not first loaded — and so a
        `version` check applies to a delete the way it does to an update.

        **It deletes, and only deletes.** Putting a record away without losing it is
        `archive`: a different fact, and a different method, because other records point at it
        and a name that lies about which of the two happened is worse than two names.
        """
        self._refuse_reentrant_write()
        self._refuse_read_only()
        records = records_of(record)
        if not records:
            return
        with self._session() as session:
            plan = planned_removal(session, records)
            newness = self._carried_out(session, plan)
        self._announced(plan, newness)

    def archive(self, record: Any) -> None:
        """Stamps when it left and keeps the row — one aggregate or several.

        What a business usually means by deleting: it has to stop appearing and cannot be
        lost, because invoices point at it. A reading leaves it out unless its criteria names
        `archived_at`. Refused for an aggregate that is not `ArchivableMixin`, which has
        nowhere to write it.
        """
        self._refuse_reentrant_write()
        records = records_of(record)
        refuse_unarchivable(records)
        for one in records:
            self._before_archive(one)
            one.archive()
        self.save(records)
        for one in records:
            self._after_archive(one)

    def _bulk_where(self, model: type, criteria: Criteria, verb: str) -> Any:
        """The WHERE a write by criteria runs under: the filter, the scope, the archived left
        out — and refused whole when any condition could not be answered, because a dropped
        condition widens a write where it only widened a read."""
        if criteria.pagination.asked:
            raise ContractViolation(
                f"{verb} writes every row its filter matches; a page cannot bound it — "
                "narrow the filter instead"
            )
        meta = describe(model)
        expression, dropped = meta.accept(self._asked(model, meta, criteria))
        if dropped:
            raise ContractViolation(
                f"{verb} cannot answer {', '.join(one.field for one in dropped)} on "
                f"{model.__name__}; a write never runs wider than it was asked"
            )
        return sql.where_clause(model, expression)

    def _executed(self, statement: Any, subject: str) -> Any:
        with self._session() as session, named(subject):
            return session.execute(statement)

    def update_all(self, target: type, criteria: Criteria, values: Mapping[str, Any]) -> int:
        """Sets these values on every row the filter matches, in one statement; the number of
        rows is the answer.

            repository.update_all(Session, Criteria(where=expired), {"state": "closed"})

        **Past the aggregate, on purpose.** No hook runs, no cascade, no change tracking, no
        version check — what Django's `update()` and Rails' `update_all` skip too. For a write
        with no rule to keep: closing expired sessions, re-stamping a column after a migration.
        What it keeps is what a stale copy needs to be refused later: `version` is raised and
        `updated_at` stamped. The scope it was narrowed to and the archived apply as in a read;
        a page, or a condition the aggregate cannot answer, is refused.
        """
        self._refuse_reentrant_write()
        self._refuse_read_only()
        model, _ = model_and_collection(target)
        clause = self._bulk_where(model, criteria, "update_all")
        columns = {prop.key for prop in sa_inspect(model).column_attrs}
        fixed = {describe(model).identity, "created_at", "version", "updated_at"}
        wrong = sorted(name for name in values if name not in columns or name in fixed)
        if wrong:
            raise ContractViolation(
                f"update_all cannot set {', '.join(wrong)} on {model.__name__}: not a column, "
                "or one the framework keeps"
            )
        changes: dict[str, Any] = dict(values)
        if "version" in columns:
            changes["version"] = cast(Any, model).version + 1
        if "updated_at" in columns:
            changes["updated_at"] = utc_now()
        statement = update(model).values(changes)
        if clause is not None:
            statement = statement.where(clause)
        result = self._executed(statement, f"update_all over {model.__name__}")
        return result.rowcount

    def remove_all(self, target: type, criteria: Criteria) -> int:
        """Deletes every row the filter matches, in one statement; the number of rows is the
        answer.

            repository.remove_all(Session, Criteria(where=expired))

        No hook, no cascade — the children a root owns are not taken with it, and a foreign key
        that still points at a row refuses the delete as a `ConstraintViolation`. For rows with
        nothing hanging from them: expired sessions, a staging table. The scope and the
        archived apply as in a read; a page, or a condition that cannot be answered, is refused.
        """
        self._refuse_reentrant_write()
        self._refuse_read_only()
        model, _ = model_and_collection(target)
        clause = self._bulk_where(model, criteria, "remove_all")
        statement = delete(model)
        if clause is not None:
            statement = statement.where(clause)
        result = self._executed(statement, f"remove_all over {model.__name__}")
        return result.rowcount

    def _returned(self, statement: Any, subject: str) -> int:
        """Runs a statement that writes back what it wrote, and counts it."""
        with self._session() as session, named(subject):
            return len(session.execute(statement).all())

    def _upserted(
        self,
        model: type,
        records: list[Any],
        on: tuple[str, ...],
        overwrite: Sequence[str] | None,
    ) -> Upserted:
        """One model's batch as one statement: refused if no upsert can write it, kept inside the
        scope, one record per key, the hooks around it, and what came back counted.

        1. What cannot be upserted is refused before anything runs (`services/upsert.py`).
        2. The batch is reduced to one record per key, the last one — Postgres refuses a
           statement that touches a row twice, and SQLite would hide it.
        3. A narrowed repository bounds the conflict by its scope and never overwrites the
           scope's own fields.
        4. Final: the rows written back are the written; the rest of the batch was skipped.
        """
        mapper = sa_inspect(model)
        table = cast(Table, mapper.local_table)
        dialect = self._dialect()
        names = {prop.key: prop.columns[0].name for prop in mapper.column_attrs}
        identity = describe(model).identity
        fixed = {identity, "created_at", "version", "updated_at"}
        upsert_sql.refuse(
            model, table, mapper.inherits is not None, dialect, names, on, overwrite, fixed
        )
        for one in records:
            self._refuse_outside(one)
        latest = upsert_sql.one_per_key(records, on)
        for one in latest:
            self._fire("before_save", one)
        scope = self._scope.expression if self._scope is not None else None
        bound = (
            sql.where_clause(model, describe(model).accept(scope)[0])
            if scope is not None
            else None
        )
        scope_fields = (
            {one.field for one in conditions_of(scope)} if scope is not None else set()
        )
        now = utc_now()
        statement = upsert_sql.statement(
            dialect,
            table,
            upsert_sql.rows(latest, names, now),
            {names[name] for name in on},
            upsert_sql.overwritten(names, on, overwrite, fixed, scope_fields),
            now,
            bound,
            names[identity],
        )
        written = self._returned(statement, f"upsert of {model.__name__}")
        for one in latest:
            self._fire("after_save", one)
        return Upserted(written=written, skipped=len(latest) - written)

    def upsert(
        self, record: Any, on: Sequence[str], update: Sequence[str] | None = None
    ) -> Upserted:
        """Inserts each record, or overwrites the stored one holding the same `on` — one
        statement per aggregate type — and answers how many were written and how many skipped.

            done = repository.upsert(lines, on=("ledger_id", "number"), update=("amount", "state"))
            done.written, done.skipped        # per distinct key: inserted or overwritten · left as is

        For the import that must be idempotent and the sync that mirrors another system: no
        read first, no race between the read and the write. **It overwrites, by definition** —
        no version check, and the records handed in are not refreshed: read them again to
        change them. `update` names the columns a conflict overwrites, every one but the key
        and the framework's own when omitted, none with `update=()`. A record is skipped when
        its conflict has nothing to overwrite, or when the stored row lies outside the scope
        this repository was narrowed to — which it never overwrites.

        `before_save` and `after_save` run; `before_create`/`before_update` do not, because
        which one happened only the database knows. No cascade and no change tracking.
        Postgres and SQLite (3.35+) are spoken; another dialect is refused.
        """
        self._refuse_reentrant_write()
        self._refuse_read_only()
        by_model: dict[type, list[Any]] = {}
        for one in records_of(record):
            by_model.setdefault(type(one), []).append(one)
        done = [
            self._upserted(model, batch, tuple(on), update)
            for model, batch in by_model.items()
        ]
        return Upserted(
            written=sum(one.written for one in done), skipped=sum(one.skipped for one in done)
        )
