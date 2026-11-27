"""`TenantOwnedMixin` — a recipe, not a requirement: it only declares the field.

    @dataclass
    class Invoice(TenantOwnedMixin, Entity):
        total: Decimal

Tenancy discriminates any aggregate with a `tenant_id` field, inherited from here or written by
hand; and one with another field by declaration (`Tenancy(field="company_id")`,
`tenancy.discriminator(...)`). The column goes on the table with `tenant_columns()`.
"""

from dataclasses import dataclass, field


@dataclass(kw_only=True)
class TenantOwnedMixin:
    """An aggregate that belongs to one tenant — the field tenancy stamps on its first save and
    narrows every read by."""

    tenant_id: str | None = field(
        default=None, metadata={"label": {"default": "Tenant", "es": "Tenant"}}
    )
