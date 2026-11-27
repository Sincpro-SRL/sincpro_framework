"""`sincpro_framework.tenancy` — multi-tenancy, opt-in and decoupled.

    from sincpro_framework.tenancy import Tenancy

    tenancy = Tenancy()
    tenancy.on(billing)                     the tenant context resolved per call, `self.tenancy`
    tenancy.isolate(database)               the data filter on the store

The pieces, each usable alone:

| Piece | What it is |
|---|---|
| `TenantContext` | the tenants an execution works in — active (`tenant_id`), selected (`tenant_ids`) |
| `TenantDiscriminator`, `Shared` | the field that partitions an aggregate, and whether an empty one is everyone's |
| `TenantFilter` | tenancy as a data filter: reads narrowed, new records stamped, writes checked |
| `Memberships`, `Membership` | the tenants a subject may select — a port |
| `TenantCatalog`, `Tenant`, `TenantStatus` | the tenants a service knows and their lifecycle — a port |
| `Tenancy` | the facade: wiring, the tenant context, scopes and declarations |
| `TenantOwnedMixin` | a recipe: an aggregate that declares `tenant_id` |

A project with a multi-tenancy of its own uses none of it: the execution context keeps
`tenant_id` and `tenant_ids` as before, and data filters take any rule.
"""

from sincpro_framework.tenancy.adapters.memory import (
    InMemoryMemberships,
    InMemoryTenantCatalog,
)
from sincpro_framework.tenancy.domain.catalog import Tenant, TenantCatalog, TenantStatus
from sincpro_framework.tenancy.domain.discriminator import Shared, TenantDiscriminator
from sincpro_framework.tenancy.domain.exceptions import (
    TenantNotAllowed,
    TenantNotChosen,
    TenantRequired,
)
from sincpro_framework.tenancy.domain.memberships import Membership, Memberships
from sincpro_framework.tenancy.domain.tenant_context import TenantContext
from sincpro_framework.tenancy.domain.tenant_owned import TenantOwnedMixin
from sincpro_framework.tenancy.entrypoint.tenancy import AcrossTenants, Tenancy
from sincpro_framework.tenancy.services.discrimination import TENANT_FILTER, TenantFilter

__all__ = [
    "AcrossTenants",
    "InMemoryMemberships",
    "InMemoryTenantCatalog",
    "Membership",
    "Memberships",
    "Shared",
    "TENANT_FILTER",
    "Tenancy",
    "Tenant",
    "TenantCatalog",
    "TenantContext",
    "TenantDiscriminator",
    "TenantFilter",
    "TenantNotAllowed",
    "TenantNotChosen",
    "TenantOwnedMixin",
    "TenantRequired",
    "TenantStatus",
]
