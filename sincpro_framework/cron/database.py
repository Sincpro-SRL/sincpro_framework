"""`DatabaseRuns`: the once-per-tick guard, shared by every replica through one table.

    runs_table = cron_run_table(metadata)          # beside the project's own tables
    CronGateway([cron_payments], runs=DatabaseRuns(database, runs_table)).run()

Context: the primary key is `(name, scheduled_for, key)`, so the first INSERT of a tick wins and
every other replica's fails — no leader election, no singleton process. Needs the `[sqlalchemy]`
extra; the table is the project's, created by its migrations like any other.
"""

from datetime import UTC, datetime

from sqlalchemy import (
    Column,
    ColumnElement,
    DateTime,
    MetaData,
    Row,
    Table,
    Text,
    insert,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError

from sincpro_framework.cron.runs import Run, RunOutcome
from sincpro_framework.orm import Database


def cron_run_table(metadata: MetaData, name: str = "cron_run") -> Table:
    return Table(
        name,
        metadata,
        Column("name", Text, primary_key=True),
        Column("scheduled_for", DateTime(timezone=True), primary_key=True),
        Column("key", Text, primary_key=True),
        Column("started_at", DateTime(timezone=True), nullable=False),
        Column("finished_at", DateTime(timezone=True)),
        Column("outcome", Text),
    )


def _as_utc(moment: datetime) -> datetime:
    """SQLite hands back what it stored without its zone; every moment here is UTC."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _aware(moment: datetime | None) -> datetime | None:
    return _as_utc(moment) if moment is not None else None


class DatabaseRuns:
    def __init__(self, database: Database, table: Table) -> None:
        self.database = database
        self.table = table

    def _run(self, row: Row) -> Run:
        return Run(
            row.name,
            _as_utc(row.scheduled_for),
            row.key,
            _as_utc(row.started_at),
            _aware(row.finished_at),
            RunOutcome(row.outcome) if row.outcome else None,
        )

    def claim(self, name: str, scheduled_for: datetime, key: str = "") -> bool:
        row = {
            "name": name,
            "scheduled_for": scheduled_for.astimezone(UTC),
            "key": key,
            "started_at": datetime.now(UTC),
        }
        try:
            with self.database.session() as session:
                session.execute(insert(self.table).values(row))
        except IntegrityError:
            return False
        return True

    def finish(
        self, name: str, scheduled_for: datetime, key: str, outcome: RunOutcome
    ) -> None:
        table = self.table
        with self.database.session() as session:
            session.execute(
                update(table)
                .where(
                    table.c.name == name,
                    table.c.scheduled_for == scheduled_for.astimezone(UTC),
                    table.c.key == key,
                )
                .values(finished_at=datetime.now(UTC), outcome=outcome.value)
            )

    def _latest(self, name: str, *conditions: ColumnElement[bool]) -> Run | None:
        table = self.table
        statement = (
            select(table)
            .where(table.c.name == name, table.c.key == "", *conditions)
            .order_by(table.c.scheduled_for.desc())
            .limit(1)
        )
        with self.database.session() as session:
            row = session.execute(statement).first()
        return self._run(row) if row is not None else None

    def running(self, name: str, since: datetime) -> bool:
        table = self.table
        unfinished = self._latest(
            name,
            table.c.finished_at.is_(None),
            table.c.scheduled_for >= since.astimezone(UTC),
        )
        return unfinished is not None

    def last(self, name: str) -> Run | None:
        return self._latest(name)

    def last_success(self, name: str) -> Run | None:
        return self._latest(name, self.table.c.outcome == RunOutcome.SUCCEEDED.value)
