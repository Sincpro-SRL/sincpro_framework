# Grants — roles declared in code, held per tenant, resolved per call

A use case requires a `Permission` ([the overview](README.md)). Who holds it comes from roles:
the ones a token carries, the ones this service assigned. This page is the catalog of roles —
grouped the way Odoo 20 groups them, for an administration screen — the stores that keep who
holds which, and the resolver that turns both into the permissions a call acts with. Every block
on this page runs, in order, in `tests/docs/test_persistence_guide.py`.

**Where each thing lives.** The identity provider (Keycloak) says who someone is and which
applications they may enter; the roles of an ERP — clerk, manager, approver, per company — are
the service's: assigned on its own screen, effective on the next call, never waiting for a token
to expire.

## The catalog

```python
from sincpro_framework.auth import Permission
from sincpro_framework.auth.grants import Category, Entitlement, GrantCatalog, Privilege, Role


class BillingPermission(Permission):
    READ = "billing.invoice.read"
    ISSUE = "billing.invoice.issue"
    APPROVE = "billing.payment.approve"
    PORTAL = "portal.invoice.read"


accounting = Category("accounting", "Accounting")
invoicing = Privilege("invoicing", "Invoicing", category=accounting, placeholder="No access")
clerk = Role("billing.clerk", "Clerk", grants={BillingPermission.READ}, privilege=invoicing, sequence=10)
manager = Role("billing.manager", "Manager", grants={BillingPermission.ISSUE}, implies=(clerk,),
               privilege=invoicing, sequence=20)
approver = Role("billing.approver", "Approver", grants={BillingPermission.APPROVE}, implies=(manager,))
portal = Role("portal", "Portal", grants={BillingPermission.PORTAL}, disjoint={"billing.clerk"})

catalog = GrantCatalog(
    [approver, portal],
    entitlements=[Entitlement("e-invoicing", gates={BillingPermission.ISSUE})],
)

assert catalog.permissions_of({"billing.approver"}) == {
    BillingPermission.READ, BillingPermission.ISSUE, BillingPermission.APPROVE}
assert catalog.conflicts({"portal", "billing.manager"}) == [
    "billing.clerk and portal are never held together"]
assert catalog.level_of("invoicing", {"billing.approver"}) == manager
```

| Piece | Odoo 20 | What it is |
|---|---|---|
| `Category` | `ir.module.category` | a section of the screen |
| `Privilege` | `res.groups.privilege` | one choice in it: which level, or `placeholder` |
| `Role` | `res.groups` | what it grants (`grants`), what it implies (`implies`) |
| a privilege's roles, by `sequence` | its groups | exclusive levels — each must imply the one below, or the catalog is a `ProgrammingError` |
| `disjoint` | `disjoint_ids` | never held together, judged on what is implied too |
| `Entitlement` | — | what a tenant bought: the permissions it `gates` are granted only to its tenants |

`catalog.describe()` is the whole catalog for a screen — categories → privileges → levels, each
role with what it implies and is implied by, the roles of no privilege — Odoo 20's
`view_group_hierarchy`, one selector per privilege. `GrantCatalog.from_role_permissions(table)`
turns a `RolePermissions` table into a catalog.

## Who holds which

A store keeps assignments — only what was given; the closure is the catalog's, so a role that
changes what it implies changes every holder at once. `GrantAdministration` checks every change
against the catalog and bumps the subject's permission version.

```python
from datetime import UTC, datetime, timedelta

from sincpro_framework.auth import InMemoryRevocations, InvalidGrant
from sincpro_framework.auth.grants import GrantAdministration, InMemoryGrants

grants = InMemoryGrants()                         # KeyValueGrants() / DatabaseGrants in production
revocations = InMemoryRevocations()               # KeyValueRevocations() in production
admin = GrantAdministration(catalog, grants, revocations)

admin.set_level("user:ana", "bo", "invoicing", "billing.manager")    # one level per privilege
admin.assign("user:ana", "bo", "billing.approver", valid_until=datetime.now(UTC) + timedelta(days=1))
admin.entitle("bo", "e-invoicing")

try:
    admin.assign("user:ana", "bo", "portal")
except InvalidGrant as refused:
    assert "never held together" in str(refused)

assert grants.held("user:ana", "bo").roles == {"billing.manager", "billing.approver"}
```

| Store | Where | Proven by |
|---|---|---|
| `InMemoryGrants` | this process | `GrantStoreContract` |
| `KeyValueGrants` | the running context's `KeyValueStore` — Redis; compare-and-set, so two administrators never lose each other's role | `GrantStoreContract` |
| `DatabaseGrants` | a table of the project's (`data_layer.orm`) | `GrantStoreContract` |

## Resolved per call

`GrantResolver` runs where a provider authenticates: the token's roles mapped to the catalog's,
the roles assigned, their closure, what it grants — gated by the tenant's entitlements and capped
by the token's scopes. A role is held only while everything it grants is: once the entitlements
or the scopes take a permission away, the roles that grant it go too, so `auth.role(...)` never
admits what the permissions refuse.

```python
from sincpro_framework.auth import Identity
from sincpro_framework.auth.grants import GrantResolver, RoleMapping, ScopeCeiling

resolver = GrantResolver(
    catalog,
    grants,
    role_mapping=RoleMapping({"app:billing": "billing.clerk"}),        # a Keycloak role
    scope_ceiling=ScopeCeiling({"billing:read": ["billing.*.read"]}, first_party={"billing-web"}),
    entitlements=True,
    revocations=revocations,
)

ana = resolver.resolve(Identity.user("user:ana", tenant="bo", scopes={"billing:read"})
                       .model_copy(update={"client": "billing-web"}))
assert ana.permissions == {BillingPermission.READ, BillingPermission.ISSUE, BillingPermission.APPROVE}

reports_app = resolver.resolve(Identity.user("user:ana", tenant="bo", scopes={"billing:read"})
                               .model_copy(update={"client": "reports-app"}))
assert reports_app.permissions == {BillingPermission.READ}           # what its scope allows
assert reports_app.roles == {"billing.clerk"}       # auth.role("billing.manager") refuses it too

admin.set_level("user:ana", "bo", "invoicing", "billing.clerk")      # taken down a level…
admin.unassign("user:ana", "bo", "billing.approver")
assert resolver.resolve(Identity.user("user:ana", tenant="bo").model_copy(
    update={"client": "billing-web"})).permissions == {BillingPermission.READ}   # …at once
```

What a subject holds is cached per subject, tenant and permission version: every change through
`GrantAdministration` bumps the version, so the next call on any replica resolves anew. Without
`revocations`, a change holds once `cache_for` (five minutes) passes. A resolver belongs to a
provider — `JwtProvider(..., resolvers=[resolver])`, or `Resolving(provider, resolver)` for one of
the project's — never to `AccessControl`: a service token or an API key arrives resolved.
