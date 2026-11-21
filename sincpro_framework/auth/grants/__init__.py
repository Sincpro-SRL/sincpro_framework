"""Roles and groups the Odoo 20 way: a catalog declared in code, who holds which as data, and
what a caller holds resolved on every call.

    from sincpro_framework.auth.grants import GrantCatalog, GrantResolver, Role

    catalog = GrantCatalog([manager, portal], entitlements=[Entitlement("e-invoicing", ...)])
    JwtProvider(..., resolvers=[GrantResolver(catalog, KeyValueGrants())])

| Piece | Odoo 20 |
|---|---|
| `Category`, `Privilege`, `Role`, `GrantCatalog` | `ir.module.category`, `res.groups.privilege`, `res.groups` |
| `Assignment`, `GrantStore` (`InMemoryGrants`, `KeyValueGrants`; `DatabaseGrants` in `data_layer.orm`) | `group_ids` — what was assigned, never the closure |
| `GrantAdministration` | the users screen: assign, take away, a level per privilege, a tenant's entitlements |
| `GrantResolver` (`RoleMapping`, `ScopeCeiling`) | `all_group_ids`, resolved per call |
| `Entitlement` | what a tenant bought |

`RolePermissions` is the short form: a table of roles to permissions, no catalog.
"""

from sincpro_framework.auth.grants.adapters.definitions import (
    InMemoryRoleDefinitions,
    KeyValueRoleDefinitions,
)
from sincpro_framework.auth.grants.adapters.live_catalog import (
    LiveCatalog,
)
from sincpro_framework.auth.grants.adapters.resolver import (
    GrantResolver,
    RoleMapping,
    ScopeCeiling,
)
from sincpro_framework.auth.grants.adapters.role_permissions import RolePermissions
from sincpro_framework.auth.grants.adapters.stores import InMemoryGrants, KeyValueGrants
from sincpro_framework.auth.grants.domain.assignments import (
    Assignment,
    GrantSource,
    GrantStore,
    Held,
)
from sincpro_framework.auth.grants.domain.catalog import (
    CatalogDescription,
    CatalogSource,
    Category,
    Composition,
    Entitlement,
    GrantCatalog,
    Privilege,
    Role,
)
from sincpro_framework.auth.grants.domain.definitions import (
    DefinedRoles,
    RoleDefinition,
    RoleDefinitions,
)
from sincpro_framework.auth.grants.domain.events import (
    EntitlementWithdrawn,
    LevelChanged,
    RoleAssigned,
    RoleChanged,
    RoleDefined,
    RoleRemoved,
    RoleUnassigned,
    TenantEntitled,
)
from sincpro_framework.auth.grants.services.administration import GrantAdministration
from sincpro_framework.auth.grants.services.role_administration import RoleAdministration

__all__ = [
    "Assignment",
    "CatalogDescription",
    "CatalogSource",
    "Category",
    "Composition",
    "DefinedRoles",
    "Entitlement",
    "EntitlementWithdrawn",
    "GrantAdministration",
    "GrantCatalog",
    "GrantResolver",
    "GrantSource",
    "GrantStore",
    "Held",
    "InMemoryGrants",
    "InMemoryRoleDefinitions",
    "KeyValueGrants",
    "KeyValueRoleDefinitions",
    "LevelChanged",
    "LiveCatalog",
    "Privilege",
    "Role",
    "RoleAdministration",
    "RoleAssigned",
    "RoleChanged",
    "RoleDefined",
    "RoleDefinition",
    "RoleDefinitions",
    "RoleMapping",
    "RolePermissions",
    "RoleRemoved",
    "RoleUnassigned",
    "ScopeCeiling",
    "TenantEntitled",
]
