"""`DatabaseIdempotencyRecords`: idempotency records on a database, the answer completed in the
write's own transaction — exactly once, not best effort.

    records = DatabaseIdempotencyRecords(database, template_table.idempotency_table(
        "idempotency", metadata
    ))

    @billing.feature(CommandCharge)
    @billing.idempotency.exactly_once(records)
    class Charge(Feature):
        def execute(self, dto: CommandCharge) -> ResponseCharge:
            with self.repository.context():        joins the unit the records opened
                ...

**The claim commits on its own; the answer commits with the write.** A claim is visible to every
replica at once — a duplicate in flight is refused, never held on the row — and `transaction()`
opens the unit of work every repository on `database` joins while the use case runs, so the
write and its completed record are one commit. A replica that dies before it leaves both rolled
back: its claim expires and the retry runs the write — the one it never made. That is the gap
`KeyValueRecords` cannot close: there the record and the write are two stores.

**Only what is written on `database` is once.** A call to another service from inside the write
is not rolled back with it — hand it the request's idempotency key, so it runs once too.

The claim and the release open a transaction of their own, so they need a connection of their
own: an in-memory SQLite has one, and holds while a unit of work is open on it.
"""

from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from functools import partial

from sqlalchemy import Table, delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from sincpro_framework.data_layer.caching.domain.idempotency_records import (
    IdempotencyRecord,
    IdempotencyRecords,
    RecordState,
)
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.repository import Repository
from sincpro_framework.data_layer.orm.sqlalchemy.infrastructure.database import Database
from sincpro_framework.data_layer.orm.sqlalchemy.infrastructure.unit_in_play import in_play
from sincpro_framework.sincpro_logger import logger


def _utc(now: datetime) -> datetime:
    """The moment as the table keeps it: UTC, with no zone, alike on every engine."""
    return now.astimezone(UTC).replace(tzinfo=None)


class DatabaseIdempotencyRecords(IdempotencyRecords):
    """Records in `table`, completed in the unit of work in play on `database`."""

    def __init__(
        self,
        database: Database,
        table: Table,
        now: Callable[[], datetime] = partial(datetime.now, UTC),
    ) -> None:
        """`now` is the clock a claim's hold and an answer's life are judged by."""
        self.database = database
        self.table = table
        self.now = now
        self._said = False

    def _moment(self) -> datetime:
        """Now, by the injected clock, as the table keeps it."""
        return _utc(self.now())

    def _row(
        self, key: str, record: IdempotencyRecord, lasting: timedelta
    ) -> dict[str, object]:
        """The row `record` is stored as under `key`, expiring `lasting` from now."""
        return {
            "key": key,
            "state": record.state.value,
            "fingerprint": record.fingerprint,
            "owner": record.owner,
            "answer": record.answer,
            "expires_at": self._moment() + lasting,
        }

    def claim(self, key: str, record: IdempotencyRecord, hold: timedelta) -> bool:
        """Committed on its own, so every replica sees it at once.

            claiming = IdempotencyRecord(state=IN_PROGRESS, fingerprint="f1", owner="a")
            records.claim("charge-42", claiming, timedelta(seconds=30))   ->  True
            records.claim("charge-42", claiming, timedelta(seconds=30))   ->  False, held
            31 seconds later, the first never completed:
            records.claim("charge-42", claiming, timedelta(seconds=30))   ->  True

        1. A record past its time is let go of first: an abandoned claim, an answer expired.
        2. Final: the claim inserted — `False` when another one is there, the primary key the
           only arbiter of who won.
        """
        row = self.table.c
        with self.database.session() as session:
            session.execute(
                delete(self.table).where(
                    (row.key == key) & (row.expires_at <= self._moment())
                )
            )
            try:
                with session.begin_nested():
                    session.execute(self.table.insert().values(self._row(key, record, hold)))
            except IntegrityError:
                return False
        return True

    def read(self, key: str) -> IdempotencyRecord | None:
        """The record under `key` still within its time — a row past `expires_at` reads as
        none, although it is only deleted by the next claim.

            records.read("charge-42")
                ->  IdempotencyRecord(state=COMPLETED, fingerprint="f1", owner="a",
                                      answer=b'{"charged": 100}')
            records.read("never-claimed")   ->  None

        Read in a session of its own: what other replicas committed, not the unit in play.
        """
        row = self.table.c
        with self.database.session() as session:
            found = session.execute(
                select(row.state, row.fingerprint, row.owner, row.answer).where(
                    (row.key == key) & (row.expires_at > self._moment())
                )
            ).first()
        if found is None:
            return None
        return IdempotencyRecord(
            state=RecordState(found.state),
            fingerprint=found.fingerprint,
            owner=found.owner,
            answer=bytes(found.answer),
        )

    @contextmanager
    def _completing(self) -> Generator[Session]:
        """The unit of work in play — the answer commits with the write — or, outside one, a
        transaction of its own, said once: the answer and the write are then two commits."""
        unit = in_play(self.database)
        if unit is not None:
            yield unit.session
            return
        if not self._said:
            self._said = True
            logger.warning(
                "an idempotency answer completed outside a unit of work commits on its own: the "
                "write and its record are two commits — run it through exactly_once(records)"
            )
        with self.database.session() as session:
            yield session

    def complete(self, key: str, record: IdempotencyRecord, keep_for: timedelta) -> None:
        """Writes the answer in the unit of work in play, so it commits with the write.

            done = IdempotencyRecord(state=COMPLETED, fingerprint="f1", owner="a",
                                     answer=b'{"charged": 100}')
            with repository.context():
                ...the write...
                records.complete("charge-42", done, timedelta(hours=24))   ->  None
            records.read("charge-42")   ->  done, once the unit committed

        1. The session: the unit in play on `database`, or — outside one — a transaction of
           its own, warned about once.
        2. The claimed row updated with the answer and its new expiry.
           2.1 No row moved (the claim was let go of meanwhile): inserted instead.
        Final: committed with the unit of work, or by its own transaction.
        """
        values = self._row(key, record, keep_for)
        with self._completing() as session:
            moved = session.execute(
                update(self.table).where(self.table.c.key == key).values(values)
            )
            if getattr(moved, "rowcount", 0) == 0:
                session.execute(self.table.insert().values(values))

    def release(self, key: str, owner: str) -> None:
        """One statement: only the claim `owner` wrote, still in progress, is removed."""
        row = self.table.c
        with self.database.session() as session:
            session.execute(
                delete(self.table).where(
                    (row.key == key)
                    & (row.owner == owner)
                    & (row.state == RecordState.IN_PROGRESS.value)
                )
            )

    @contextmanager
    def transaction(self) -> Generator[None]:
        """The unit of work the run and its answer share: every repository on `database`
        joins it — one already in play is joined in turn."""
        with Repository(self.database).context():
            yield
