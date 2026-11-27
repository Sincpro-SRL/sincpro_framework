"""The tables of tenancy — recipes a project declares in its metadata and migrations, like any
other table. Nothing in tenancy requires them: a column named by hand works the same.

    entity_table("invoice", metadata, *tenant_columns(), unique_per_tenant("number"))
    event_table("billing_events", metadata, *tenant_columns())
    tenant_table("tenant", metadata)                  DatabaseTenantCatalog
    membership_table("tenant_membership", metadata)   DatabaseMemberships

Context: a unique value is unique per tenant — two tenants both have an invoice 0001 — and a
reference never crosses tenants when the foreign key carries the tenant too (the composite key
Citus and django-multitenant use: `tenant_foreign_key`).
"""

from collections.abc import Sequence

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKeyConstraint,
    Index,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
)

from sincpro_framework.tenancy.domain.catalog import TenantStatus


def tenant_columns(field: str = "tenant_id", nullable: bool = True) -> list[Column]:
    """The tenant discriminator of a table, indexed — every read of it filters by it.

    out     tenant_id TEXT NULL, INDEX
    """
    return [Column(field, Text, nullable=nullable, index=True)]


def unique_per_tenant(
    *columns: str, field: str = "tenant_id", name: str | None = None
) -> UniqueConstraint:
    """A value unique inside each tenant, repeated across them.

    out     UNIQUE (tenant_id, number)
    """
    return UniqueConstraint(field, *columns, name=name)


def tenant_foreign_key(
    columns: Sequence[str],
    references: Sequence[str],
    field: str = "tenant_id",
    referenced_field: str | None = None,
    name: str | None = None,
) -> ForeignKeyConstraint:
    """A reference that cannot cross tenants: the foreign key carries the tenant on both sides,
    so a row of one tenant pointing at a row of another is refused by the database itself.

        tenant_foreign_key(["customer_id"], ["customer.id"])
        out     FOREIGN KEY (tenant_id, customer_id) REFERENCES customer (tenant_id, id)

    The referenced table needs a unique key on (tenant_id, id): `unique_per_tenant("id")`.
    """
    table = references[0].rsplit(".", 1)[0]
    return ForeignKeyConstraint(
        [field, *columns],
        [f"{table}.{referenced_field or field}", *references],
        name=name,
    )


def tenant_table(name: str, metadata: MetaData) -> Table:
    """The tenant catalog: one row per tenant.

    out     id TEXT PRIMARY KEY · name TEXT · status TEXT ('active', …)
            · placement JSON TEXT · created_at TIMESTAMP (UTC)
    """
    return Table(
        name,
        metadata,
        Column("id", Text, primary_key=True),
        Column("name", Text, nullable=False, default=""),
        Column("status", Text, nullable=False, default=TenantStatus.ACTIVE.value),
        Column("placement", Text, nullable=False, default="{}"),
        Column("created_at", DateTime, nullable=True),
        Index(f"ix_{name}_status", "status"),
    )


def membership_table(name: str, metadata: MetaData) -> Table:
    """Who belongs to which tenant, with which role.

    out     subject · tenant (PRIMARY KEY together) · role TEXT NULL · INDEX (tenant)
    """
    return Table(
        name,
        metadata,
        Column("subject", Text, primary_key=True),
        Column("tenant", Text, primary_key=True),
        Column("role", Text, nullable=True),
        Index(f"ix_{name}_tenant", "tenant"),
    )
