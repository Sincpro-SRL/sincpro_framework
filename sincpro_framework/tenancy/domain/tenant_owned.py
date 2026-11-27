"""`TenantOwnedMixin` — a declaration written as a base class: it adds `tenant_id`, and tenancy
partitions what inherits it. The column goes on the table with `tenant_columns()`.

    @dataclass
    class Invoice(TenantOwnedMixin, Entity):
        total: Decimal

Context: a recipe, not a requirement — `tenancy.discriminator(Invoice)` declares the same of a
`tenant_id` written by hand, and `field=` another name.
"""

from dataclasses import dataclass, field


@dataclass(kw_only=True)
class TenantOwnedMixin:
    """An aggregate that belongs to one tenant."""

    tenant_id: str | None = field(
        default=None, metadata={"label": {"default": "Tenant", "es": "Tenant"}}
    )
