"""Tenancy's ports on a database — the tenant catalog and the memberships — each on a template
table the project declares (`template_table.tenant_table`, `template_table.membership_table`),
proven by the same contract as the ones in memory.

    DatabaseTenantCatalog(database, template_table.tenant_table("tenant", metadata))
    DatabaseMemberships(database, template_table.membership_table("tenant_membership", metadata))

Context: they read and write the catalog itself, a shared table — never through the tenant
filter, which would narrow the list of tenants to the one asking.
"""

import json
from collections.abc import Mapping
from datetime import UTC, datetime

from sqlalchemy import Table, delete, select
from sqlalchemy.dialects import postgresql, sqlite

from sincpro_framework.data_layer.orm.sqlalchemy.infrastructure.database import Database
from sincpro_framework.tenancy.domain.catalog import Tenant, TenantCatalog, TenantStatus
from sincpro_framework.tenancy.domain.memberships import Membership, Memberships


def _upsert(database: Database, table: Table, values: dict, keys: list[str]) -> None:
    """One row written whole: inserted, or replaced when its key is held."""
    dialect = database.engine.dialect.name
    if dialect in ("postgresql", "sqlite"):
        insert = (postgresql if dialect == "postgresql" else sqlite).insert(table)
        statement = insert.values(**values).on_conflict_do_update(
            index_elements=keys,
            set_={name: value for name, value in values.items() if name not in keys},
        )
        with database.session() as session:
            session.execute(statement)
        return
    with database.session() as session:
        session.execute(
            delete(table).where(*(table.c[name] == values[name] for name in keys))
        )
        session.execute(table.insert().values(**values))


class DatabaseTenantCatalog(TenantCatalog):
    """The tenants in `table`."""

    def __init__(self, database: Database, table: Table) -> None:
        self.database = database
        self.table = table

    def get(self, tenant_id: str) -> Tenant | None:
        with self.database.session() as session:
            row = session.execute(
                select(self.table).where(self.table.c.id == tenant_id)
            ).first()
        if row is None:
            return None
        return Tenant(row.id, row.name, TenantStatus(row.status), json.loads(row.placement))

    def ids(self, status: TenantStatus = TenantStatus.ACTIVE) -> list[str]:
        with self.database.session() as session:
            return list(
                session.scalars(
                    select(self.table.c.id)
                    .where(self.table.c.status == status.value)
                    .order_by(self.table.c.id)
                )
            )

    def put(self, tenant: Tenant) -> None:
        """Adds the tenant, or replaces the one of the same id — a status change included."""
        _upsert(
            self.database,
            self.table,
            {
                "id": tenant.id,
                "name": tenant.name,
                "status": tenant.status.value,
                "placement": json.dumps(dict(tenant.placement), sort_keys=True),
                "created_at": datetime.now(UTC).replace(tzinfo=None),
            },
            ["id"],
        )


class DatabaseMemberships(Memberships):
    """Who belongs to which tenant, in `table`."""

    def __init__(self, database: Database, table: Table) -> None:
        self.database = database
        self.table = table

    def tenants_of(self, subject: str) -> Mapping[str, Membership]:
        with self.database.session() as session:
            rows = session.execute(
                select(self.table).where(self.table.c.subject == subject)
            ).all()
        return {row.tenant: Membership(row.tenant, row.role) for row in rows}

    def members_of(self, tenant: str) -> dict[str, str | None]:
        """Every subject of `tenant`, with its role."""
        with self.database.session() as session:
            rows = session.execute(
                select(self.table).where(self.table.c.tenant == tenant)
            ).all()
        return {row.subject: row.role for row in rows}

    def join(self, subject: str, tenant: str, role: str | None = None) -> None:
        """Makes `subject` a member of `tenant`, with `role` — replacing the role it had there."""
        _upsert(
            self.database,
            self.table,
            {"subject": subject, "tenant": tenant, "role": role},
            ["subject", "tenant"],
        )

    def leave(self, subject: str, tenant: str) -> None:
        """Takes `subject` out of `tenant`."""
        with self.database.session() as session:
            session.execute(
                delete(self.table).where(
                    self.table.c.subject == subject, self.table.c.tenant == tenant
                )
            )
