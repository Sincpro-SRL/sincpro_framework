"""`DatabaseNumbering`: gapless numbers on a database, from a counter row written in the unit
of work that uses them.

    counters = numbering_table("numbering", metadata)
    numbering = DatabaseNumbering(database, counters)

    with repository.context() as unit:
        numbers = numbering.take("F", count=3, scope="branch-1/2026")    range(41, 44)
        …
    an exception inside  →  the counter is back at 40: nothing was taken

**One statement, so there is no read to race.** On Postgres and SQLite the counter is taken by
`INSERT … ON CONFLICT DO UPDATE SET last = last + n RETURNING last`: the row is created on the
first take and locked by the write on every other, so a second transaction waits on it rather than
reading the same number. Elsewhere it is an `UPDATE` first, an `INSERT` in a savepoint when there
was no row, and the value read back under the lock the `UPDATE` holds.

**Gapless inside a unit of work.** Taken outside one, a number commits on its own and the record
carrying it is saved later — a failure between them is a hole. That is allowed and said in the
log; the promise of no gaps holds inside the `context()` that saves the record.
"""

from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Table, select, update
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from sincpro_framework.data_layer import exceptions
from sincpro_framework.data_layer.orm.sqlalchemy.infrastructure.database import Database
from sincpro_framework.data_layer.orm.sqlalchemy.infrastructure.unit_in_play import in_play
from sincpro_framework.ddd.repositories.numbering import INumbering, refuse_no_count
from sincpro_framework.ddd.repositories.repository import refuse_writing_in_preview
from sincpro_framework.sincpro_logger import logger

WITH_UPSERT: dict[str, Callable[[Table], Any]] = {
    "postgresql": postgresql.insert,
    "sqlite": sqlite.insert,
}
"""The dialects that take a counter in one statement."""


class DatabaseNumbering(INumbering):
    """Gapless numbers from `table`, in the unit of work in play on `database`."""

    def __init__(self, database: Database, table: Table) -> None:
        """`table` is the counter table `numbering_table` declares: one row per
        (series, scope), holding the last number taken."""
        self.database = database
        self.table = table

    @contextmanager
    def _session(self) -> Generator[Session]:
        """The unit of work in play — the numbers commit with what carries them — or, outside
        one, a transaction of their own, said in the log: a failure after it leaves a gap."""
        unit = in_play(self.database)
        if unit is not None:
            if unit.transaction.read_only:
                raise exceptions.ProgrammingError(
                    "this unit of work was opened read_only=True; taking a number writes"
                )
            yield unit.session
            return
        logger.warning(
            "numbers taken outside a unit of work commit on their own: a failure before what "
            "carries them is saved leaves a gap — take them inside its context()"
        )
        with self.database.session() as session:
            yield session

    def _upserted(
        self,
        session: Session,
        upsert: Callable[[Table], Any],
        series: str,
        count: int,
        scope: str,
    ) -> int:
        """The counter moved in one `INSERT … ON CONFLICT DO UPDATE … RETURNING last`.

        Context: there is no read to race — the first take creates the row with `last=count`,
        every later one adds `count` under the row lock the write takes.

            ("F", 3, "branch-1/2026") on a counter at 40   ->  43
        """
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
        """The counter moved where the dialect has no upsert: UPDATE first, INSERT when there
        was no row.

        1. `UPDATE … SET last = last + count` on the (series, scope) row — it locks the row.
        2. No row moved: the row inserted with `last=count` in a savepoint.
           2.1 Answered `count` at once: these were the first numbers of the series.
           2.2 The insert lost to another transaction (`IntegrityError`): the savepoint is
               rolled back and the UPDATE run again, now on its row.
        Final: `last` read back under the lock the UPDATE holds.
        """
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

    def take(self, series: str, count: int = 1, scope: str = "") -> range:
        """Take `count` gapless numbers of `series` within `scope`.

        Context: an invoice number must never repeat nor skip; the counter row is written in
        the unit of work that saves what carries the number, so a rollback gives the numbers
        back.

            numbering.take("F", count=3, scope="branch-1/2026")   ->  range(41, 44)

        1. Refused inside `previewing()` and for a count below one.
        2. The session: the unit of work in play (refused when read-only), or a transaction of
           its own, warned in the log.
        3. The counter moved in one statement where the dialect has an upsert (Postgres,
           SQLite).
           3.1 Elsewhere: UPDATE first, INSERT in a savepoint when there was no row.
        Final: the range ending at the counter's new `last`.
        """
        refuse_writing_in_preview("take")
        refuse_no_count(count)
        with self._session() as session:
            dialect = session.get_bind().dialect.name
            upsert = WITH_UPSERT.get(dialect)
            last = (
                self._upserted(session, upsert, series, count, scope)
                if upsert is not None
                else self._updated_or_inserted(session, series, count, scope)
            )
        return range(last - count + 1, last + 1)
