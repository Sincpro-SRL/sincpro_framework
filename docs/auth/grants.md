# Grants — `sincpro_framework.auth.grants`

**Door**: `from sincpro_framework.auth.grants import …` — folder `auth/grants/`. Roles declared in
code, held per tenant, resolved per call.

A use case requires a `Permission` ([the overview](README.md)). Who holds it comes from roles:
the ones a token carries, the ones this service assigned. This page is the catalog of roles —
grouped the way Odoo 20 groups them, for an administration screen — the stores that record who
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

## Who holds which — `Grants`

`Grants` is the one object a project holds for roles: what a provider resolves each identity
with, what the administration screen calls, and the roles defined at runtime. A store records the
assignments — only what was given; the closure is the catalog's, so a role that changes what it
implies changes every holder at once. Every change is checked against the catalog and bumps the
subject's permission version.

```python
from datetime import UTC, datetime, timedelta

from sincpro_framework.auth import Identity, InvalidGrant, PermissionDenied, security
from sincpro_framework.auth.grants import Grants
from sincpro_framework.data_layer.store import InMemoryKeyValue

store = InMemoryKeyValue()                        # the context's store when not given: Redis
grants = Grants(catalog, store=store)

with security.as_system("the platform opens tenant bo"):
    grants.set_level("user:ana", "invoicing", "billing.manager", tenant="bo")   # one level
    grants.assign("user:ana", "billing.approver", tenant="bo",
                  until=datetime.now(UTC) + timedelta(days=1))
    grants.entitle("bo", "e-invoicing")
    try:
        grants.assign("user:ana", "portal", tenant="bo")
    except InvalidGrant as refused:
        assert "never held together" in str(refused)

assert {one.role for one in grants.assignments_of("user:ana", "bo")} == {
    "billing.manager", "billing.approver"}

bo_admin = Identity.user(
    "user:root-bo", tenant="bo", roles={"billing.clerk"}, permissions={BillingPermission.READ},
)                                                   # bo's administrator: a clerk
with security.as_identity(bo_admin):
    grants.assign("user:bea", "billing.clerk", tenant="bo")       # it holds the role it gives
    try:
        grants.assign("user:bea", "billing.manager", tenant="bo") # a manager issues; it does not
    except PermissionDenied as refused:
        assert "which user:root-bo does not hold" in str(refused)
```

One store holds everything — assignments, the roles defined at runtime, revocations, the
administration leases: the running context's `KeyValueStore` (Redis) when none is given,
`DatabaseKeyValue` for the project's own database. `assignments=` puts the assignments elsewhere:

| `GrantStore` | Where | Proven by |
|---|---|---|
| `KeyValueGrants` (the default) | the store given, or the context's; compare-and-set, so two administrators never lose each other's role | `GrantStoreContract` |
| `InMemoryGrants` | this process | `GrantStoreContract` |
| `DatabaseGrants` | a table of the project's (`data_layer.orm`) | `GrantStoreContract` |

**Who administers is bounded** — `Grants(..., administration=…)`, an `AdministrationPolicy`;
`BoundedAdministration()` by default:

| Who acts | May |
|---|---|
| the system (`security.as_system`), a holder of `BoundedAdministration(superadmin=…)` | anything, in any tenant, and what every tenant shares |
| anyone else known | only in their own tenant, never on themselves, and only give — or take away — a role they hold, or one their tenant defined from what they hold |
| nobody known | nothing: a seed or a migration runs `security.as_system(reason)` |

Entitlements — what a tenant bought — are the platform's. Giving is judged by roles, not only
permissions: a role that grants nothing may still be what `auth.role(...)` asks for. Whoever may
give a role may take it away, so a weaker administrator never strips a stronger one.
`TrustedAdministration()` trusts whoever is known, as Odoo trusts its access-rights group; a
project whose rule is altogether different implements `AdministrationPolicy`.

**Rules of the project's own** sit beside the bound: `@grants.allow` asks a function of every
change — a `GrantChange`: who acts, the action, the tenant, the subject, the role, the definition
— and `False` refuses it, naming the rule. The system passes.

```python
from sincpro_framework.auth.grants import GrantChange


@grants.allow
def people_only(change: GrantChange) -> bool:
    """Roles go to people; a service calls with a service token."""
    return change.subject is None or change.subject.startswith("user:")


with security.as_identity(bo_admin):
    try:
        grants.assign("service:exporter", "billing.clerk", tenant="bo")
    except PermissionDenied as refused:
        assert "people_only" in str(refused)
```

**Every change is published** when `Grants(..., publisher=…)` is given one: `RoleAssigned`,
`RoleUnassigned`, `LevelChanged`, `TenantEntitled`, `EntitlementWithdrawn` — who it is about, what
changed, and `actor`, the administrator who did it. Together they are the audit trail an ISO 27001
or SOC 2 review asks for: who gave whom access, and when
([the audit trail](README.md#an-audit-trail-what-auth-publishes)).

## Resolved per call

`Grants` is an `IdentityResolver`: it runs where a provider authenticates —
`JwtProvider(..., resolvers=[grants])` — the token's roles mapped to the catalog's, the roles
assigned, their closure, what it grants, gated by the tenant's entitlements and capped by the
token's scopes. A role is held only while everything it grants is: once the entitlements or the
scopes take a permission away, the roles that grant it go too, so `auth.role(...)` never admits
what the permissions refuse.

```python
from sincpro_framework.auth.grants import RoleMapping, ScopeCeiling

grants = Grants(                                     # the same store: the same assignments
    catalog,
    store=store,
    role_mapping=RoleMapping({"app:billing": "billing.clerk"}),        # a Keycloak role
    scope_ceiling=ScopeCeiling({"billing:read": ["billing.*.read"]}, first_party={"billing-web"}),
    entitlements=True,
)

ana = grants.resolve(Identity.user("user:ana", tenant="bo", scopes={"billing:read"})
                     .model_copy(update={"client": "billing-web"}))
assert ana.permissions == {BillingPermission.READ, BillingPermission.ISSUE, BillingPermission.APPROVE}

reports_app = grants.resolve(Identity.user("user:ana", tenant="bo", scopes={"billing:read"})
                             .model_copy(update={"client": "reports-app"}))
assert reports_app.permissions == {BillingPermission.READ}           # what its scope allows
assert reports_app.roles == {"billing.clerk"}       # auth.role("billing.manager") refuses it too

with security.as_system("support takes ana down a level"):
    grants.set_level("user:ana", "invoicing", "billing.clerk", tenant="bo")   # down a level…
    grants.unassign("user:ana", "billing.approver", tenant="bo")
assert grants.resolve(Identity.user("user:ana", tenant="bo").model_copy(
    update={"client": "billing-web"})).permissions == {BillingPermission.READ}   # …at once
```

What a subject holds is cached per subject, tenant and permission version: every change bumps the
version, so the next call on any replica resolves anew. A resolver belongs to a provider —
`JwtProvider(..., resolvers=[grants])`, or `ResolvingProvider(provider, grants)` for one of the project's
— never to `AccessControl`: a service token or an API key arrives resolved. When another system
records who holds which — nothing to administer here — `GrantResolver(catalog, source)` resolves
from that `GrantSource` alone.


## Roles defined at runtime

An administrator adds a role from a screen — a reviewer, an external auditor — without a deploy:
the platform's, for every tenant (`tenant=None`), or a tenant's own, for that tenant alone, held
in the catalog as `<key>@<tenant>` and named by its key. It sits beside the roles of the code in
the catalog in effect for each tenant, stored on the same store (`definitions=` puts it elsewhere).

```python
with security.as_system("the platform adds a role every tenant has"):
    grants.define("billing.reviewer", name="Reviewer", grants={BillingPermission.READ})
    try:
        grants.define("billing.clerk", name="Clerk, renamed")
    except InvalidGrant as refused:
        assert "declared in code" in str(refused)

with security.as_identity(bo_admin):            # tenant bo's administrator, its own role
    grants.define("cash.viewer", tenant="bo", implies={"billing.clerk"})
    grants.assign("user:carla", "cash.viewer", tenant="bo")       # held as cash.viewer@bo

carla = grants.resolve(Identity.user("user:carla", tenant="bo").model_copy(
    update={"client": "billing-web"}))                             # first party: no ceiling
assert carla.permissions == {BillingPermission.READ}
assert "cash.viewer@bo" in carla.roles
assert "cash.viewer@bo" in grants.catalog("bo").roles
assert "cash.viewer@bo" not in grants.catalog("pe").roles
assert [one.key for one in grants.defined()] == ["billing.reviewer"]   # every tenant's
assert [one.key for one in grants.defined("bo")] == ["cash.viewer"]   # bo's own
```

| Rule | Why |
|---|---|
| It only adds: a role the code declares is never redefined, and a defined role grants only permissions the code declares (every `Permission` class loaded; `permissions=` narrows it) | a role of the code changes in code, reviewed with the use cases that require what it grants; a permission no code checks protects nothing |
| It is checked by the catalog's own rules: what it implies and its privilege exist, no cycle, disjoint roles, a level implying the one below — and it never holds together, through what it implies, roles that are disjoint | the catalog in effect holds exactly as one declared in code would |
| A level — a role of a `privilege` — is every tenant's: the code's or the platform's, never a tenant's own | one selector per privilege for every tenant, and a level the platform adds never breaks a tenant's role |
| A change is checked and written while the tenant's administration lease is held, against the scope stored now — never a replica's cached catalog; assigning, defining and removing in a tenant are one at a time on every replica | nothing is removed, nor changes shape, between a check and its write |
| A change that would leave another role broken is refused; a scope holds at most 256 roles, a role implies at most 32 | an administrator never takes from another role's holders what it could not take; composing stays cheap for every tenant |
| Defining is bounded like assigning: in its own tenant only, implying only roles who acts may give, granting only what it holds; changing or removing a role is taking it away, so only who may give it does | whoever may define a role cannot give themselves, nor strip from others, more than they hold |
| A role held — given, or held through a tenant role implying it — retains its shape: what it implies, its privilege, level and disjoint roles; a role every tenant shares never changes shape | its holders never hold together what `assign` refuses |
| A tenant's role is held by its tenant's own name — `cash.viewer@bo` — never by a key of the code or of every tenant: `auth.role("billing.admin")` and a requirement of the platform are never met by a role a tenant named so; a name the catalog declares is always that role | one customer's administrators never give themselves, nor anyone, a role the code or the platform requires |
| A removed key is retired in its scope, never defined again nor given; a tenant's role removed takes its assignments with it; an assignment left of any removed role grants nothing and meets no `auth.role`. A key the code declares after it was used at runtime is listed in `problems` and no old assignment grants it — rename it in code | assignments left behind never come back to life, not even through a deploy |
| A role changed so it may take something away — fewer grants, fewer roles implied — or removed bumps its scope's permission version, the tenant's or every tenant's: a token carrying permissions (`pv`) is refused there, whoever held the role directly or through another. Saved again as it is, it bumps again; a removal asked again finishes what a failure left | a token never carries what a role no longer grants; a failure after the write is recovered by asking again |
| Every replica reads a tenant's versions at most once a `check_interval` (a second), one caller per tenant, never holding back another, and composes again only when one moved | a role defined holds within a second everywhere, for small reads; a slow tenant slows nobody else |
| A defined role that no longer builds grants nothing, the others still do; `grants.problems(tenant)` lists which and why — a tenant role implying one every tenant shares that was removed among them — and whoever administers the scope may fix or remove it | a broken definition never grants more than before, nor takes away what the others give, nor stays stuck |
| A store that does not answer leaves the last catalog composed in effect, asked again only once a `check_interval` passed — a tenant never composed is the code's until it answers | an outage never grants more, retains what was granted, and never grows into a queue of timeouts |

Store them on a durable store — Redis with persistence, or `DatabaseKeyValue`: a store that loses
a document loses the roles and the retired keys it held (a replica never answers with what it
built of the lost one). With more than one replica, the administration leases are on the same
store — `Grants(store=...)` puts everything on one; a store of this process alone is said once in
the log.

A screen shows `grants.catalog(tenant).describe()` — the whole catalog of the tenant —
`grants.defined(tenant)` the roles it may edit, and `grants.problems(tenant)` the ones to fix.
With a publisher, every change is published: `RoleDefined`, `RoleChanged`, `RoleRemoved`, and the
`RoleUnassigned` of a removal.
