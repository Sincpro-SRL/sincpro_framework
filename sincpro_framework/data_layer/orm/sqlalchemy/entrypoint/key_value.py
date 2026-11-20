"""`DatabaseKeyValue`: a `KeyValueStore` on a table of the project's database — for a service with
no Redis that still wants what auth keeps on a store shared by its replicas: revocations, failed
logins, one-time links, TOTP steps, leases.

    store = DatabaseKeyValue(database, template_table.key_value_table("auth_key_value", metadata))
    KeyValueRevocations(store) · LoginAttempts(store) · OneTimeTokens(store)

Context: every operation is a short transaction of its own; the atomic ones — `add`, `increment`,
`take`, `replace` — are decided by the primary key or by a compare-and-set `UPDATE`/`DELETE`
whose row count says who won, retried when another writer moved first. A row past its
`expires_at` reads as absent and is overwritten as if it were.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

from sqlalchemy import Table, delete, select, update
from sqlalchemy.exc import IntegrityError

from sincpro_framework.data_layer.orm.sqlalchemy.infrastructure.database import Database
from sincpro_framework.data_layer.store import KeyValueStore

ATTEMPTS = 64


class DatabaseKeyValue(KeyValueStore):
    """Keys, values and expiries in `table`."""

    def __init__(
        self,
        database: Database,
        table: Table,
        now: Callable[[], datetime] = partial(datetime.now, UTC),
    ) -> None:
        """`now` is the clock expiries are judged by — a `ManualClock.now` in a test."""
        self.database = database
        self.table = table
        self.now = now

    def _moment(self) -> datetime:
        """Now, as the table stores it: UTC with no zone, alike on every engine."""
        return self.now().astimezone(UTC).replace(tzinfo=None)

    def _until(self, ttl: timedelta | None) -> datetime | None:
        return None if ttl is None else self._moment() + ttl

    def _live(self) -> Any:
        """Rows not past their expiry."""
        row = self.table.c
        return row.expires_at.is_(None) | (row.expires_at > self._moment())

    def _expired(self, key: str) -> Any:
        row = self.table.c
        return (
            (row.key == key)
            & row.expires_at.is_not(None)
            & (row.expires_at <= self._moment())
        )

    def get_many(self, keys: list[str]) -> list[bytes | None]:
        """One `SELECT … WHERE key IN (…)` of the live rows, answered in the order of `keys`."""
        if not keys:
            return []
        row = self.table.c
        with self.database.session() as session:
            found = {
                one.key: bytes(one.value)
                for one in session.execute(
                    select(row.key, row.value).where(row.key.in_(keys) & self._live())
                )
            }
        return [found.get(key) for key in keys]

    def set(self, key: str, value: bytes, ttl: timedelta | None = None) -> None:
        """An `UPDATE`, an `INSERT` when there was no row — again when a racing insert won."""
        row = self.table.c
        for _ in range(ATTEMPTS):
            try:
                with self.database.session() as session:
                    written = session.execute(
                        update(self.table)
                        .where(row.key == key)
                        .values(value=value, expires_at=self._until(ttl))
                    )
                    if getattr(written, "rowcount", 0) == 0:
                        session.execute(
                            self.table.insert().values(
                                key=key, value=value, expires_at=self._until(ttl)
                            )
                        )
                return
            except IntegrityError:
                continue

    def add(self, key: str, value: bytes, ttl: timedelta | None = None) -> bool:
        """The expired row deleted, then an `INSERT` — the primary key refuses every racer but
        one."""
        with self.database.session() as session:
            session.execute(delete(self.table).where(self._expired(key)))
            try:
                with session.begin_nested():
                    session.execute(
                        self.table.insert().values(
                            key=key, value=value, expires_at=self._until(ttl)
                        )
                    )
            except IntegrityError:
                return False
        return True

    def increment(self, key: str, ttl: timedelta | None = None) -> int:
        """The live value read and written back on the condition it is unchanged — again when
        raced; a missing or expired counter inserted at 1."""
        row = self.table.c
        for _ in range(ATTEMPTS):
            with self.database.session() as session:
                held = session.execute(
                    select(row.value).where((row.key == key) & self._live())
                ).first()
                if held is None:
                    session.execute(delete(self.table).where(self._expired(key)))
                    try:
                        with session.begin_nested():
                            session.execute(
                                self.table.insert().values(
                                    key=key, value=b"1", expires_at=self._until(ttl)
                                )
                            )
                    except IntegrityError:
                        continue
                    return 1
                counted = int(bytes(held.value)) + 1
                values: dict[str, Any] = {"value": str(counted).encode()}
                if ttl is not None:
                    values["expires_at"] = self._until(ttl)
                written = session.execute(
                    update(self.table)
                    .where((row.key == key) & (row.value == bytes(held.value)) & self._live())
                    .values(**values)
                )
                if getattr(written, "rowcount", 0) == 1:
                    return counted
        raise RuntimeError(f"the counter {key} is contended past {ATTEMPTS} attempts")

    def delete(self, key: str) -> None:
        """The row deleted — nothing when it is not there."""
        with self.database.session() as session:
            session.execute(delete(self.table).where(self.table.c.key == key))

    def take(self, key: str) -> bytes | None:
        """The live value read, then deleted on the condition it still holds it — of many
        racing, one deletes it."""
        row = self.table.c
        for _ in range(ATTEMPTS):
            with self.database.session() as session:
                held = session.execute(
                    select(row.value).where((row.key == key) & self._live())
                ).first()
                if held is None:
                    return None
                taken = session.execute(
                    delete(self.table).where(
                        (row.key == key) & (row.value == bytes(held.value))
                    )
                )
                if getattr(taken, "rowcount", 0) == 1:
                    return bytes(held.value)
        return None

    def replace(
        self, key: str, expected: bytes, value: bytes | None, ttl: timedelta | None = None
    ) -> bool:
        """One conditional `UPDATE` — a `DELETE` for `None` — of the live row holding
        `expected`."""
        row = self.table.c
        matching = (row.key == key) & (row.value == expected) & self._live()
        with self.database.session() as session:
            if value is None:
                done = session.execute(delete(self.table).where(matching))
            else:
                done = session.execute(
                    update(self.table)
                    .where(matching)
                    .values(value=value, expires_at=self._until(ttl))
                )
        return getattr(done, "rowcount", 0) == 1

    def touch(self, key: str, ttl: timedelta | None) -> bool:
        """One conditional `UPDATE` of a live row's expiry — `False` when missing or expired."""
        row = self.table.c
        with self.database.session() as session:
            done = session.execute(
                update(self.table)
                .where((row.key == key) & self._live())
                .values(expires_at=self._until(ttl))
            )
        return getattr(done, "rowcount", 0) == 1

    def shared(self) -> bool:
        """Always: every replica reads the same database."""
        return True
