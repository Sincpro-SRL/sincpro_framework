"""Auth's stores on a database — API keys, grants, refresh tokens — each on a template table the
project declares (`template_table.api_key_table`, …), proven by the same contract as the stores
in memory and on Redis.

    DatabaseApiKeys(database, template_table.api_key_table("api_key", metadata))
    DatabaseGrants(database, template_table.grant_assignment_table("grant_assignment", metadata),
                   template_table.entitlement_table("entitlement", metadata))
    DatabaseRefreshTokens(database, template_table.refresh_token_table("refresh_token", metadata))

Context: what changes atomically is decided by the database — a primary key for a new row, a
compare-and-set `UPDATE` (`rotated_at IS NULL`) for a rotation — so replicas sharing it race
safely. A tenant of none is stored as `""`.
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from functools import partial
from typing import Any

from sqlalchemy import Table, delete, select, update
from sqlalchemy.exc import IntegrityError

from sincpro_framework.auth.accounts import RefreshRecord, RefreshTokens
from sincpro_framework.auth.grants import Assignment, GrantStore
from sincpro_framework.auth.server import ApiKey, ApiKeyStore
from sincpro_framework.data_layer.orm.sqlalchemy.infrastructure.database import Database


def _stored(moment: datetime | None) -> datetime | None:
    """A moment as the tables keep it: UTC, no zone."""
    return None if moment is None else moment.astimezone(UTC).replace(tzinfo=None)


def _read(moment: datetime | None) -> datetime | None:
    """A moment read back: UTC, with its zone."""
    return None if moment is None else moment.replace(tzinfo=UTC)


class DatabaseApiKeys(ApiKeyStore):
    """API keys in `table`, by digest."""

    def __init__(self, database: Database, table: Table) -> None:
        self.database = database
        self.table = table

    def find(self, digest: str) -> ApiKey | None:
        """The row of `digest` — `None` when there is none."""
        row = self.table.c
        with self.database.session() as session:
            found = session.execute(select(self.table).where(row.digest == digest)).first()
        if found is None:
            return None
        return ApiKey(
            subject=found.subject,
            tenant=found.tenant,
            permissions=frozenset(json.loads(found.permissions)),
            scopes=frozenset(json.loads(found.scopes)),
            digest=found.digest,
            revoked=bool(found.revoked),
            expires_at=_read(found.expires_at),
        )

    def save(self, key: ApiKey) -> None:
        """An `UPDATE` by its digest — an `INSERT` when it is new."""
        values = {
            "subject": key.subject,
            "tenant": key.tenant,
            "permissions": json.dumps(sorted(key.permissions)),
            "scopes": json.dumps(sorted(key.scopes)),
            "revoked": key.revoked,
            "expires_at": _stored(key.expires_at),
        }
        with self.database.session() as session:
            written = session.execute(
                update(self.table).where(self.table.c.digest == key.digest).values(**values)
            )
            if getattr(written, "rowcount", 0) == 0:
                session.execute(self.table.insert().values(digest=key.digest, **values))


class DatabaseGrants(GrantStore):
    """Assignments in `assignments`, entitlements in `entitlements`."""

    def __init__(
        self,
        database: Database,
        assignments: Table,
        entitlements: Table,
        now: Callable[[], datetime] = partial(datetime.now, UTC),
    ) -> None:
        self.database = database
        self.assignments = assignments
        self.entitlements = entitlements
        self._now = now

    def now(self) -> datetime:
        """The moment an assignment's `valid_until` is judged against."""
        return self._now()

    def _assignment(self, found: Any) -> Assignment:
        return Assignment(
            subject=found.subject,
            tenant=found.tenant or None,
            role=found.role,
            valid_until=_read(found.valid_until),
        )

    def assign(self, assignment: Assignment) -> None:
        """An `UPDATE` of its validity when the role was given already, else an `INSERT` — a
        racing insert's row stands."""
        row = self.assignments.c
        tenant = assignment.tenant or ""
        with self.database.session() as session:
            written = session.execute(
                update(self.assignments)
                .where(
                    (row.subject == assignment.subject)
                    & (row.tenant == tenant)
                    & (row.role == assignment.role)
                )
                .values(valid_until=_stored(assignment.valid_until))
            )
            if getattr(written, "rowcount", 0) == 0:
                try:
                    with session.begin_nested():
                        session.execute(
                            self.assignments.insert().values(
                                subject=assignment.subject,
                                tenant=tenant,
                                role=assignment.role,
                                valid_until=_stored(assignment.valid_until),
                            )
                        )
                except IntegrityError:
                    pass

    def unassign(self, subject: str, tenant: str | None, role: str) -> None:
        """The row deleted — nothing when there was none."""
        row = self.assignments.c
        with self.database.session() as session:
            session.execute(
                delete(self.assignments).where(
                    (row.subject == subject)
                    & (row.tenant == (tenant or ""))
                    & (row.role == role)
                )
            )

    def assignments_of(self, subject: str, tenant: str | None) -> list[Assignment]:
        """The subject's rows in `tenant` — no tenant kept as `""` — ordered by role."""
        row = self.assignments.c
        with self.database.session() as session:
            found = session.execute(
                select(self.assignments)
                .where((row.subject == subject) & (row.tenant == (tenant or "")))
                .order_by(row.role)
            ).all()
        return [self._assignment(one) for one in found]

    def holders(self, role: str, tenant: str | None) -> list[Assignment]:
        """The role's rows in `tenant`, ordered by subject."""
        row = self.assignments.c
        with self.database.session() as session:
            found = session.execute(
                select(self.assignments)
                .where((row.role == role) & (row.tenant == (tenant or "")))
                .order_by(row.subject)
            ).all()
        return [self._assignment(one) for one in found]

    def entitle(self, tenant: str, entitlement: str) -> None:
        """An `INSERT` — a duplicate, refused by the key, is the same entitlement."""
        with self.database.session() as session:
            try:
                with session.begin_nested():
                    session.execute(
                        self.entitlements.insert().values(
                            tenant=tenant, entitlement=entitlement
                        )
                    )
            except IntegrityError:
                pass

    def withdraw(self, tenant: str, entitlement: str) -> None:
        """The row deleted — nothing when there was none."""
        row = self.entitlements.c
        with self.database.session() as session:
            session.execute(
                delete(self.entitlements).where(
                    (row.tenant == tenant) & (row.entitlement == entitlement)
                )
            )

    def entitlements_of(self, tenant: str | None) -> frozenset[str]:
        """The tenant's rows."""
        row = self.entitlements.c
        with self.database.session() as session:
            found = session.execute(
                select(row.entitlement).where(row.tenant == (tenant or ""))
            )
            return frozenset(one.entitlement for one in found)


class DatabaseRefreshTokens(RefreshTokens):
    """Refresh tokens in `table`, rotated by a compare-and-set `UPDATE`."""

    def __init__(
        self,
        database: Database,
        table: Table,
        now: Callable[[], datetime] = partial(datetime.now, UTC),
    ) -> None:
        self.database = database
        self.table = table
        self.now = now

    def _values(self, record: RefreshRecord) -> dict[str, object]:
        return {
            "digest": record.digest,
            "session": record.session,
            "subject": record.subject,
            "record": record.model_dump_json(),
            "rotated_at": _stored(record.rotated_at),
            "session_ends": _stored(record.session_ends or record.expires_at),
        }

    def _record(self, found: Any) -> RefreshRecord:
        record = RefreshRecord.model_validate_json(found.record)
        return record.model_copy(update={"rotated_at": _read(found.rotated_at)})

    def save(self, record: RefreshRecord) -> None:
        """An `INSERT` — a digest is saved once."""
        with self.database.session() as session:
            session.execute(self.table.insert().values(**self._values(record)))

    def find(self, digest: str) -> RefreshRecord | None:
        """The row of `digest` — `None` when unknown."""
        with self.database.session() as session:
            found = session.execute(
                select(self.table).where(self.table.c.digest == digest)
            ).first()
        return None if found is None else self._record(found)

    def rotate(self, digest: str, successor: RefreshRecord, at: datetime) -> bool:
        """One conditional `UPDATE` of a row not rotated yet, and the successor inserted in
        the same transaction — of racing rotations exactly one updates the row."""
        row = self.table.c
        with self.database.session() as session:
            done = session.execute(
                update(self.table)
                .where((row.digest == digest) & row.rotated_at.is_(None))
                .values(rotated_at=_stored(at))
            )
            if getattr(done, "rowcount", 0) != 1:
                return False
            session.execute(self.table.insert().values(**self._values(successor)))
        return True

    def sessions_of(self, subject: str) -> list[RefreshRecord]:
        """The subject's rows not rotated, and of them the ones still live — oldest first."""
        row = self.table.c
        now = self.now()
        with self.database.session() as session:
            found = session.execute(
                select(self.table).where((row.subject == subject) & row.rotated_at.is_(None))
            ).all()
        records = [self._record(one) for one in found]
        return sorted(
            (one for one in records if now < one.ends_at),
            key=lambda one: one.issued_at,
        )
