"""`Numbering` on a database: gapless numbers from a counter row, written in the unit of work
that uses them.

    counters = numbering_table("numbering", metadata)
    numbering = Numbering(database, counters)

    with repository.context() as unit:
        numbers = numbering.take("F", count=3, scope="branch-1/2026")    range(41, 44)
        …
    an exception inside  →  the counter is back at 40: nothing was taken

**One statement, so there is no read to race.** On Postgres and SQLite the counter is taken by
`INSERT … ON CONFLICT DO UPDATE SET last = last + n RETURNING last`: the row is created on the
first take and locked by the write on every other, so a second transaction waits on it rather than
reading the same number. Elsewhere it is an `UPDATE` first, an `INSERT` in a savepoint when there
was no row, and the value read back under the lock the `UPDATE` holds.

**Only inside a unit of work.** Taken outside one, the number would be committed on its own and
the record carrying it saved later — a failure between them is the hole this exists to prevent.
"""

from collections.abc import Callable
from typing import Any

from sqlalchemy import Table, select, update
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from sincpro_framework.ddd.exceptions import ContractViolation
from sincpro_framework.ddd.repositories.numbering import Numbering as BaseNumbering
from sincpro_framework.ddd.repositories.numbering import refuse_no_count
from sincpro_framework.orm.sqlalchemy.infrastructure.database import Database
from sincpro_framework.orm.sqlalchemy.infrastructure.unit_in_play import in_play

WITH_UPSERT: dict[str, Callable[[Table], Any]] = {
    "postgresql": postgresql.insert,
    "sqlite": sqlite.insert,
}
"""The dialects that take a counter in one statement."""


class Numbering(BaseNumbering):
    """Gapless numbers from `table`, in the unit of work in play on `database`."""

    def __init__(self, database: Database, table: Table) -> None:
        self.database = database
        self.table = table

    def take(self, series: str, count: int = 1, scope: str = "") -> range:
        refuse_no_count(count)
        session = self._session()
        dialect = session.get_bind().dialect.name
        upsert = WITH_UPSERT.get(dialect)
        last = (
            self._upserted(session, upsert, series, count, scope)
            if upsert is not None
            else self._updated_or_inserted(session, series, count, scope)
        )
        return range(last - count + 1, last + 1)

    def _session(self) -> Session:
        unit = in_play(self.database)
        if unit is None:
            raise ContractViolation(
                "numbers are taken inside the context() that saves what carries them: taken "
                "outside one, they commit on their own and a failure after leaves a gap"
            )
        if unit.transaction.read_only:
            raise ContractViolation(
                "this unit of work was opened read_only=True; taking a number writes"
            )
        return unit.session

    def _upserted(
        self,
        session: Session,
        upsert: Callable[[Table], Any],
        series: str,
        count: int,
        scope: str,
    ) -> int:
        counter = self.table.c
        statement = (
            upsert(self.table)
            .values(series=series, scope=scope, last=count)
            .on_conflict_do_update(
                index_elements=[counter.series, counter.scope],
                set_={"last": counter.last + count},
            )
            .returning(counter.last)
        )
        return int(session.execute(statement).scalar_one())

    def _updated_or_inserted(
        self, session: Session, series: str, count: int, scope: str
    ) -> int:
        counter = self.table.c
        here = (counter.series == series) & (counter.scope == scope)
        moved = session.execute(
            update(self.table).where(here).values(last=counter.last + count)
        )
        if getattr(moved, "rowcount", 0) == 0:
            try:
                with session.begin_nested():
                    session.execute(
                        self.table.insert().values(series=series, scope=scope, last=count)
                    )
                return count
            except IntegrityError:
                # Another transaction created the row first: take from it like everyone else.
                session.execute(
                    update(self.table).where(here).values(last=counter.last + count)
                )
        return int(session.execute(select(counter.last).where(here)).scalar_one())
