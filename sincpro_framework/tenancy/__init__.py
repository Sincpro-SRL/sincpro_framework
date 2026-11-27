"""`sincpro_framework.tenancy` — multi-tenancy, opt-in and decoupled.

    from sincpro_framework.tenancy import Tenancy

    tenancy = Tenancy()
    tenancy.discriminator(Invoice)          what is partitioned by tenant
    tenancy.on(billing)                     the tenant context resolved per call, `self.tenancy`

    @tenancy.isolated                       a use case that works inside its tenant

The pieces, each usable alone:

| Piece | What it is |
|---|---|
| `TenantContext` | the tenants an execution works in — active (`tenant_id`), selected (`tenant_ids`) |
| `TenantDiscriminator`, `Shared` | the field that partitions an aggregate, and whether an empty one is everyone's |
| `TenantFilter`, `Discriminators` | tenancy as a data filter applied to an execution: reads narrowed, new records stamped, writes outside not found |
| `WhenMissing` | what an isolated use case does with no tenant: `RAISE`, `SKIP`, `SHARED` |
| `Memberships`, `Membership` | the tenants a subject may select — a port |
| `TenantCatalog`, `Tenant`, `TenantStatus` | the tenants a service knows and their lifecycle — a port |
| `Tenancy`, `Door` | the doors to each use case: a decorator for a use case, a `with` for a block, `self.tenancy` |
| `IsolatedRepository` | one repository whose every call runs isolated |
| `TenantOwnedMixin` | a recipe: an aggregate that declares `tenant_id` |
| `TenantUseCases` and its DTOs | the built-in use cases: the caller's tenant context, the catalog, the memberships |

A project with a multi-tenancy of its own uses none of it: the execution context keeps
`tenant_id` and `tenant_ids` as before, and data filters take any rule.
"""

from sincpro_framework.tenancy.adapters.memory import (
    InMemoryMemberships,
    InMemoryTenantCatalog,
)
from sincpro_framework.tenancy.domain.catalog import Tenant, TenantCatalog, TenantStatus
from sincpro_framework.tenancy.domain.discriminator import (
    Shared,
    TenantDiscriminator,
    WhenMissing,
)
from sincpro_framework.tenancy.domain.exceptions import (
    TenantNotAllowed,
    TenantRequired,
)
from sincpro_framework.tenancy.domain.memberships import Membership, Memberships
from sincpro_framework.tenancy.domain.tenant_context import TenantContext
from sincpro_framework.tenancy.domain.tenant_owned import TenantOwnedMixin
from sincpro_framework.tenancy.entrypoint.tenancy import Door, Tenancy, TenantDeclaration
from sincpro_framework.tenancy.entrypoint.use_cases import (
    GetTenantContext,
    JoinTenant,
    LeaveTenant,
    ListTenantMembers,
    ListTenants,
    PutTenant,
    ResponseMembership,
    ResponseTenant,
    ResponseTenantContext,
    ResponseTenantMembers,
    ResponseTenants,
    TenantUseCases,
)
from sincpro_framework.tenancy.services.discrimination import (
    TENANT_FILTER,
    Discriminators,
    TenantFilter,
)
from sincpro_framework.tenancy.services.views import IsolatedRepository

__all__ = [
    "Discriminators",
    "Door",
    "GetTenantContext",
    "JoinTenant",
    "LeaveTenant",
    "ListTenantMembers",
    "ListTenants",
    "PutTenant",
    "ResponseMembership",
    "ResponseTenant",
    "ResponseTenantContext",
    "ResponseTenantMembers",
    "ResponseTenants",
    "TenantUseCases",
    "InMemoryMemberships",
    "InMemoryTenantCatalog",
    "IsolatedRepository",
    "Membership",
    "Memberships",
    "Shared",
    "TENANT_FILTER",
    "Tenancy",
    "Tenant",
    "TenantCatalog",
    "TenantContext",
    "TenantDeclaration",
    "TenantDiscriminator",
    "TenantFilter",
    "TenantNotAllowed",
    "TenantOwnedMixin",
    "TenantRequired",
    "TenantStatus",
    "WhenMissing",
]
