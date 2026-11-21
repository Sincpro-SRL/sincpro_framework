# PRD_36: Administering auth at runtime — roles and requirements changed without a deploy

- **Status**: built on `feat/315-runtime-administration` (#315, #316), documented in
  [docs/auth/grants.md](../auth/grants.md#roles-defined-at-runtime) and
  [docs/auth/README.md](../auth/README.md#requirements-added-at-runtime).
- **Builds on**: PRD_34 (grants, revocations), PRD_35 (one door per job), #314 (auth events).
- **Reviewed**: three independent security reviews (10, 15, then 15 findings); every finding
  answered by the design below — or listed under known limits — and held by a test in
  `tests/auth/test_runtime_roles.py`, `tests/auth/test_runtime_requirements.py`,
  `tests/auth/test_grants_facade.py` and `tests/auth/test_token_issuer.py`.

## Problem

Who may do what changed only with a deploy. Assigning a role was data, but defining a role ("an
external auditor") or asking more of a use case ("refunds need an approver") was code. Odoo, the
reference of the team, treats groups as data; a SaaS needs each customer to shape its own roles;
and an enterprise review (ISO 27001, SOC 2) asks who gave whom access, and that nobody could give
themselves more.

## The API — two objects a project holds

```python
grants = Grants(catalog, publisher=audit)                # roles: resolve, assign, define
JwtProvider(issuer, resolvers=[grants])
grants.assign("user:ana", "billing.manager", tenant="bo", until=friday)
grants.define("cash.desk", tenant="bo", implies={"billing.clerk"})   # bo's own: cash.desk@bo
grants.assign("user:bea", "cash.desk", tenant="bo")                    # named by its key

@grants.allow                                            # a rule of the project's own
def owners_give_admin(change: GrantChange) -> bool: ...

auth = AccessControl[BillingPermission](providers=jwt, runtime=True, publisher=audit)
auth.runtime.require(CommandRefund, BillingPermission.APPROVE, tenant="bo")
auth.runtime.require(CommandRefund, auth.role("cash.lead@bo"), tenant="bo")
auth.runtime.release(CommandRefund, tenant="bo")
```

- **`Grants`** (`auth.grants`) is the facade of roles: an `IdentityResolver` for the providers,
  and the administration screen's object. One `store` holds everything — assignments, the roles
  defined, revocations, the leases: the context's (Redis) when not given. `assignments=`,
  `revocations=`, `definitions=` replace one part; `role_mapping=`, `scope_ceiling=`,
  `entitlements=` shape what an identity resolves to; permissions that may be granted default to
  every `Permission` the code declares (`declared_permissions()`).
- **`AccessControl(runtime=True)`** keeps what administrators require at runtime; `auth.runtime`
  is the `RuntimeRequirements` that changes it. `runtime=` also takes an `AddedRequirementStore`.
- **Who may** is one knob on both: `administration=` — an `AdministrationPolicy`,
  `BoundedAdministration(superadmin=...)` by default, `TrustedAdministration()` for Odoo's rule, a
  class of the project's for any other. **Rules of the project's own** are callbacks:
  `@grants.allow` asks a function of every `GrantChange`; `auth.every_call` stays the callback
  of every call.
- What composes them — `LiveCatalog`, `GrantAdministration`, `RoleAdministration`,
  `AdministrationLocks`, the documents — is internal: one name, one door.

## What other systems do

| System | Roles and access as data | How a change reaches every worker | Who may administer |
|---|---|---|---|
| Odoo 20 | `res.groups`, `ir.model.access`, `ir.rule` rows | `transaction._check_signaling(cr)` reads the `orm_signaling_*` sequences at the start of every transaction (`odoo/orm/environments.py`) — the next request | the access-rights group, trusted entirely |
| Keycloak | realm and client roles, groups, composite roles | the token: a change holds at the next token | fine-grained admin permissions — an administrator manages only what it is given |
| AWS IAM | policies, roles | eventually consistent, seconds | permission boundaries: a delegated administrator never grants beyond its boundary |

## Decisions

1. **Roles and requirements only add to the code.** A role the code declares is never redefined;
   a permission no code declares is never granted nor required; a requirement added never
   loosens what a use case declares. A mistake on a screen never opens what the code closed.
2. **Two scopes.** What every tenant shares is the platform's (`tenant=None`); a tenant defines
   its own. A tenant's role lives in its tenant's own names — `cash.desk@bo`, its catalog key —
   so it can never meet a role of the code or of the platform: no tenant satisfies
   `auth.role("billing.admin")`, nor a platform requirement, with a role it named so itself.
3. **Administration is bounded by default** — `AdministrationPolicy`, a port:
   `BoundedAdministration` (the default; Keycloak's and AWS's rule) and `TrustedAdministration`
   (Odoo's). Bounded: the system and a superadmin may do anything; anyone else only in its own
   tenant, never on itself, and only gives — or takes away — a role it holds, or one its tenant
   defined from what it holds (`givable`). Judged by roles, not only permissions: a role that
   grants nothing may be what `auth.role` asks for. Nobody known administers nothing.
4. **A change is checked against what is stored, while the tenant's lease is held.**
   `AdministrationLocks` — a lease per tenant on the context's store — makes assigning, defining
   and removing in a tenant one at a time on every replica, so nothing is removed, nor changes
   shape, between a check and its write. Writes are compare-and-swap over one versioned document
   per scope; five collisions are a `ConcurrentChange` (409).
5. **A change never breaks another role.** A definition that would leave a role that built
   before broken is refused, and no defined role holds together, through what it implies, roles
   that are disjoint. A held role — given, or held through a tenant role implying it — keeps its
   shape (implies, privilege, level, disjoint); a role every tenant shares never changes shape —
   its holders are not listed at once. A level is every tenant's (the code's or the platform's),
   never a tenant's own, so a level added for every tenant never breaks a tenant's role.
6. **A key removed is dead.** It is retired, never defined nor given again; a tenant role removed
   takes its assignments; an assignment left of any retired key grants nothing and meets no
   `auth.role` — the composition carries the retired keys and the resolver drops them. A key the
   code declares after it was used at runtime is a problem to rename, never granted by an old
   assignment.
7. **Tokens follow at once, by scope.** A token carrying permissions carries `pv`: the subject's
   permission version, its tenant's and every tenant's, summed. A role changed so it may take
   something away, or removed, bumps its scope's version — the tenant's or every tenant's — so
   every such token there is refused, whoever held the role directly or through another; an
   entitlement changed bumps the tenant's. Holders are never walked.
8. **Each step after a write can be asked again.** A definition saved again as it is bumps again;
   a removal asked again finishes what a failure left (its holders, the bump) — the events of
   what was written are published right after it.
9. **Reading is cheap and fails safe.** `LiveCatalog` only reads: a tenant's versions at most
   once a second (`check_every`), one caller per tenant, never holding back another tenant;
   composed once per fingerprint — versions and content, so a store that lost a document is never
   answered from what was built of it — one pass when everything builds, which writes guarantee.
   A store that does not answer leaves the last value, asked again only once a second — a scope
   never read too; a tenant never read is the code's catalog (fewer grants) and, for
   requirements, refused with 503 — what only restricts fails closed. A scope holds at most 256
   roles, a role implies at most 32; whether an administrator may give a role is judged once per
   role, never per path.
10. **Every change is audited** — `RoleDefined`, `RoleChanged`, `RoleRemoved`, `RoleUnassigned`,
    `RequirementAdded`, `RequirementRemoved` (#314) — a role's whole shape, and what it was;
    nothing for a definition saved as it was. A publisher that fails is logged with the whole
    event and never undoes a change.
11. **A use case is named by its Command's dotted name** — `AccessCall.command`, `describe()`'s
    key — so two bounded contexts with a Command of one name never share a requirement. A copy
    for a test (`auth.using(...)`) judges them with its own providers.

## Pieces, one job each

| Piece | Layer | Job |
|---|---|---|
| `AdministrationPolicy`, `BoundedAdministration`, `TrustedAdministration` | `auth/domain` | who may administer what |
| `ScopedDocuments`, `rewritten` / `InMemoryDocuments`, `KeyValueDocuments` | `auth/domain`, `auth/adapters` | one versioned document per scope, compare-and-swap |
| `Refreshed` | `auth/infrastructure` | what a scope builds to, per scope, failing as its user says |
| `AdministrationLocks` | `auth/infrastructure` | one administration of a tenant at a time — said once in the log when its store is this process's alone |
| `RoleDefinition`, `DefinedRoles`, `compose`, `built`, `givable`, `definable`, `GrantChange`, `allowed` | `auth/grants/domain` | the rules, pure |
| `LiveCatalog` | `auth/grants/adapters` | reads the catalog in effect per tenant |
| `GrantAdministration`, `RoleAdministration` | `auth/grants/services` | assign and entitle; define and remove |
| `Grants` | `auth/grants/entrypoint` | the facade a project holds |
| `RuntimeRequirements` (`AccessControl(runtime=True)`, `auth.runtime`) | `auth/entrypoint` | requirements added, judged on every call |

## Known limits

- An administrator's grants given to others outlive the administrator's own access, as in Odoo
  and Keycloak; the audit events say who gave what.
- A role every tenant shares, removed, leaves the tenant roles that implied it broken (listed in
  `problems(tenant)`, fixable or removable by the tenant): the platform cannot list every
  tenant's roles at once.
- A requirement refuses every administrator who lacks it; one who holds it, or the platform,
  releases it. A tenant role a requirement names, removed, leaves it unmet by anyone until the
  platform releases it — release it first.
- A replica holds what it composed for up to `check_every` (a second): a token issued there in
  that second carries what was in effect before.
- The store must be durable (Redis with persistence, `DatabaseKeyValue`): a store that loses a
  document loses what it held — roles, requirements, retired keys.
