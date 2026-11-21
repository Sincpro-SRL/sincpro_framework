# Grants — `sincpro_framework.auth.grants`

**Door**: `from sincpro_framework.auth.grants import …` — folder `auth/grants/`. Roles declared in
code, held per tenant, resolved per call.

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

from sincpro_framework.auth import (
    Identity, InMemoryRevocations, InvalidGrant, PermissionDenied, security,
)
from sincpro_framework.auth.grants import GrantAdministration, InMemoryGrants

grants = InMemoryGrants()                         # KeyValueGrants() / DatabaseGrants in production
revocations = InMemoryRevocations()               # KeyValueRevocations() in production
admin = GrantAdministration(catalog, grants, revocations)

with security.as_system("the platform opens tenant bo"):
    admin.set_level("user:ana", "bo", "invoicing", "billing.manager")    # one level per privilege
    admin.assign("user:ana", "bo", "billing.approver",
                 valid_until=datetime.now(UTC) + timedelta(days=1))
    admin.entitle("bo", "e-invoicing")
    try:
        admin.assign("user:ana", "bo", "portal")
    except InvalidGrant as refused:
        assert "never held together" in str(refused)

assert grants.held("user:ana", "bo").roles == {"billing.manager", "billing.approver"}

bo_admin = Identity.user(
    "user:root-bo", tenant="bo", roles={"billing.clerk"}, permissions={BillingPermission.READ},
)                                                   # bo's administrator: a clerk
with security.as_identity(bo_admin):
    admin.assign("user:bea", "bo", "billing.clerk")          # it holds the role it gives
    try:
        admin.assign("user:bea", "bo", "billing.manager")    # a manager issues; it does not
    except PermissionDenied as refused:
        assert "which user:root-bo does not hold" in str(refused)
```

**Who administers is bounded** — `GrantAdministration(..., policy=…)`, an
`AdministrationPolicy`; `BoundedAdministration()` by default:

| Who acts | May |
|---|---|
| the system (`security.as_system`), a holder of `BoundedAdministration(superadmin=…)` | anything, in any tenant, and what every tenant shares |
| anyone else known | only in their own tenant, never on themselves, and only give — or take away — a role they hold, or one their tenant defined from what they hold |
| nobody known | nothing: a seed or a migration runs `security.as_system(reason)` |

Entitlements — what a tenant bought — are the platform's. Giving is judged by roles, not only
permissions: a role that grants nothing may still be what `auth.role(...)` asks for. Whoever may
give a role may take it away, so a weaker administrator never strips a stronger one.
`TrustedAdministration()` trusts whoever is known, as Odoo trusts its access-rights group; a
project with rules of its own implements `AdministrationPolicy`.

| Store | Where | Proven by |
|---|---|---|
| `InMemoryGrants` | this process | `GrantStoreContract` |
| `KeyValueGrants` | the running context's `KeyValueStore` — Redis; compare-and-set, so two administrators never lose each other's role | `GrantStoreContract` |
| `DatabaseGrants` | a table of the project's (`data_layer.orm`) | `GrantStoreContract` |

**Every change is published** when `GrantAdministration(..., publisher=…)` is given one:
`RoleAssigned`, `RoleUnassigned`, `LevelChanged`, `TenantEntitled`, `EntitlementWithdrawn` — who
it is about, what changed, and `actor`, the administrator who did it. Together they are the
audit trail an ISO 27001 or SOC 2 review asks for: who gave whom access, and when
([the audit trail](README.md#an-audit-trail-what-auth-publishes)).

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

with security.as_system("support takes ana down a level"):
    admin.set_level("user:ana", "bo", "invoicing", "billing.clerk")  # taken down a level…
    admin.unassign("user:ana", "bo", "billing.approver")
assert resolver.resolve(Identity.user("user:ana", tenant="bo").model_copy(
    update={"client": "billing-web"})).permissions == {BillingPermission.READ}   # …at once
```

What a subject holds is cached per subject, tenant and permission version: every change through
`GrantAdministration` bumps the version, so the next call on any replica resolves anew. Without
`revocations`, a change holds once `cache_for` (five minutes) passes. A resolver belongs to a
provider — `JwtProvider(..., resolvers=[resolver])`, or `Resolving(provider, resolver)` for one of
the project's — never to `AccessControl`: a service token or an API key arrives resolved.


## Roles defined at runtime

An administrator adds a role from a screen — a reviewer, an external auditor — without a deploy:
the platform's, for every tenant (`tenant=None`), or a tenant's own, for that tenant alone.
It sits beside the roles of the code in a `LiveCatalog`, the catalog in effect, and a
`RoleDefinitions` store keeps it: `KeyValueRoleDefinitions()` on the context's store (Redis), on
`DatabaseKeyValue` for the project's own database, `InMemoryRoleDefinitions` in a test.

```python
from sincpro_framework.auth.grants import (
    KeyValueRoleDefinitions, LiveCatalog, RoleAdministration, RoleDefinition,
)
from sincpro_framework.data_layer.store import InMemoryKeyValue

live = LiveCatalog(catalog, KeyValueRoleDefinitions(InMemoryKeyValue()), permissions=BillingPermission)
roles = RoleAdministration(live, grants, revocations)          # defines, removes
live_admin = GrantAdministration(live, grants, revocations)    # assigns, as before

with security.as_system("the platform adds a role every tenant has"):
    roles.define(RoleDefinition(
        key="billing.reviewer", name="Reviewer", grants=frozenset({BillingPermission.READ}),
    ))
    try:
        roles.define(RoleDefinition(key="billing.clerk", name="Clerk, renamed"))
    except InvalidGrant as refused:
        assert "declared in code" in str(refused)

with security.as_identity(bo_admin):            # tenant bo's administrator, its own role
    roles.define(RoleDefinition(
        key="cash.viewer", tenant="bo", implies=frozenset({"billing.clerk"}),
    ))
    live_admin.assign("user:carla", "bo", "cash.viewer")

carla = GrantResolver(live, grants).resolve(Identity.user("user:carla", tenant="bo"))
assert carla.permissions == {BillingPermission.READ}
assert "cash.viewer" in live.current("bo").roles and "cash.viewer" not in live.current("pe").roles
assert [one.key for one in live.defined()] == ["billing.reviewer"]   # every tenant's
assert [one.key for one in live.defined("bo")] == ["cash.viewer"]   # bo's own
```

| Rule | Why |
|---|---|
| It only adds: a role the code declares is never redefined, and a defined role grants only permissions the code declares (`permissions=`, every value of the project's `Permission` classes) | a role of the code changes in code, reviewed with the use cases that require what it grants; a permission no code checks protects nothing |
| It is checked by the catalog's own rules: what it implies and its privilege exist, no cycle, disjoint roles, a level implying the one below | the catalog in effect holds exactly as one declared in code would |
| A change is checked inside the write, against the scope stored now — never a replica's cached catalog; two administrators over one version: one wins, the other is checked again | a role cannot be made to imply what was defined a moment ago elsewhere, unseen |
| Defining is bounded like assigning: in its own tenant only, implying only roles who acts may give, granting only what it holds; changing or removing a role is taking it away, so only who may give it does | whoever may define a role cannot give themselves, nor strip from others, more than they hold |
| A role already assigned keeps its shape — what it implies, its privilege, level and disjoint roles; a role every tenant shares never changes shape | its holders never hold together what `assign` refuses |
| A tenant's role exists for that tenant alone, never named like one every tenant shares; one every tenant shares defined later with its name is shadowed there — the tenant's own is in effect | one customer's holders never get what nobody assigned them |
| A removed key is retired in its scope, never defined again; a tenant's role removed takes its assignments with it | assignments left behind never come back to life |
| Every replica reads a tenant's versions at most once a `check_every` (a second), one caller per tenant, never holding back another, and composes again only when one moved | a role defined holds within a second everywhere, for small reads; a slow tenant slows nobody else |
| A defined role that no longer builds grants nothing, the others still do; `live.problems(tenant)` lists which and why — a tenant role implying one every tenant shares that was removed among them | a broken definition never grants more than before, nor takes away what the others give |
| A store that does not answer leaves the last catalog composed in effect — a tenant never composed is the code's until it answers | an outage never grants more, and keeps what was granted |

`GrantResolver` and `GrantAdministration` take the `LiveCatalog` where they took the catalog; each
identity resolves in its tenant's catalog. `RoleAdministration` defines and removes. A screen
shows `live.current(tenant).describe()` — the whole catalog of the tenant — `live.defined(tenant)`
the roles it may edit, and `live.problems(tenant)` the ones to fix. With a publisher, every change
is published: `RoleDefined`, `RoleChanged`, `RoleRemoved`, and the `RoleUnassigned` of a
removal.
