"""Roles and groups the Odoo 20 way: a catalog declared in code, who holds which as data, roles
defined at runtime, and what a caller holds resolved on every call — `Grants`, one object.

    from sincpro_framework.auth.grants import GrantCatalog, Grants, Role

    catalog = GrantCatalog([manager, portal], entitlements=[Entitlement("e-invoicing", ...)])
    grants = Grants(catalog, publisher=audit)                  # the context's store
    JwtProvider(..., resolvers=[grants])
    grants.assign("user:ana", "billing.manager", tenant="bo")
    grants.define("cash.desk", tenant="bo", implies={"billing.clerk"})

| Piece | Odoo 20 |
|---|---|
| `Category`, `Privilege`, `Role`, `GrantCatalog` | `ir.module.category`, `res.groups.privilege`, `res.groups` |
| `Grants` — `assign`, `unassign`, `set_level`, `entitle`, `withdraw` | the users screen |
| `Grants` — `define`, `remove`, `catalog`, `defined`, `problems` | groups created from the UI |
| `Grants.resolve`, `GrantResolver` (`RoleMapping`, `ScopeCeiling`) | `all_group_ids`, resolved per call |
| `Assignment`, `GrantStore` (`InMemoryGrants`, `KeyValueGrants`; `DatabaseGrants` in `data_layer.orm`) | `group_ids` — what was assigned, never the closure |
| `RoleDefinition`, `RoleDefinitions` (`InMemoryRoleDefinitions`, `KeyValueRoleDefinitions`) | a `res.groups` row |
| `Entitlement` | what a tenant bought |

`GrantResolver` alone resolves roles another system keeps (`GrantSource`), with nothing to
administer. `RolePermissions` is the short form: a table of roles to permissions, no catalog.
"""

from sincpro_framework.auth.grants.adapters.definitions import (
    InMemoryRoleDefinitions,
    KeyValueRoleDefinitions,
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
    Category,
    Entitlement,
    GrantCatalog,
    Privilege,
    Role,
)
from sincpro_framework.auth.grants.domain.changes import GrantChange
from sincpro_framework.auth.grants.domain.definitions import (
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
from sincpro_framework.auth.grants.entrypoint.grants import Grants

__all__ = [
    "Assignment",
    "CatalogDescription",
    "Category",
    "Entitlement",
    "EntitlementWithdrawn",
    "GrantCatalog",
    "GrantChange",
    "GrantResolver",
    "GrantSource",
    "GrantStore",
    "Grants",
    "Held",
    "InMemoryGrants",
    "InMemoryRoleDefinitions",
    "KeyValueGrants",
    "KeyValueRoleDefinitions",
    "LevelChanged",
    "Privilege",
    "Role",
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
