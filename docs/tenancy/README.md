# Tenancy — many customers, one service, none seeing another

`sincpro_framework.tenancy` partitions what a bounded context reads and writes by tenant: a
customer, a company, an organization. It is opt-in and stands alone — a bus without it reads
`tenant_id` from its context as always, and a project with a multi-tenancy of its own keeps it.

## What it is

Three ideas, each with the name the industry gives it:

| Idea | What it says | Where it lives |
|---|---|---|
| **Tenant context** | the tenants an execution works in | the execution context: `tenant_id` (active) and `tenant_ids` (selected) |
| **Tenant discriminator** | the field that tells one tenant's records from another's | derived from the aggregate's field — no base class required |
| **Data filter** | every read narrowed, every new record stamped, every write checked | the store: `Database(filters=...)`, `MemoryRepository(filters=...)` |

The tenant context holds three sets, the shape Odoo gives a company:

| Set | Key | Meaning | Who says it |
|---|---|---|---|
| active | `tenant_id` | where a write goes | the caller, validated |
| selected | `tenant_ids` | what a read sees (`IN`) | the caller, validated |
| allowed | — | what the caller may select | `Memberships`, never the caller |

active ∈ selected ⊆ allowed. A caller that asks for *some* of its tenants adds a condition on
the tenant field to its own `Criteria`: the filter is AND-ed, so a caller's criteria only
narrows, never widens.

## How it is used — step by step

**1. One line: the context names the tenant.**

```python
from sincpro_framework.tenancy import Tenancy

tenancy = Tenancy()
database = tenancy.isolate(Database(url))     # the tenant filter on the store
tenancy.on(billing)                           # resolved per call; self.tenancy in handlers

with billing.context({"tenant_id": "acme"}):
    billing(CommandIssueInvoice(total=100))   # stamped acme; every read sees acme only
```

Any aggregate with a `tenant_id` field is partitioned — written by hand or inherited from the
`TenantOwnedMixin` recipe. Reading one with no tenant named is `TenantRequired`: reading every
tenant because none was named is the leak this exists to prevent.

**2. Options of the same declaration.**

```python
Tenancy(field="company_id")                     # another field
Tenancy(shared=Shared.WHEN_EMPTY)               # an empty tenant is everyone's (Odoo's company_id = False)
tenancy.discriminator(Branch, field="company_id")
tenancy.discriminator(Project, criteria=lambda tenants: Project.seen_by(tenants.selected))
tenancy.shared(Currency)                        # a catalog every tenant reads
```

**3. What a use case declares.**

```python
@billing.feature(CommandMonthlyClose)
@tenancy.required                               # no active tenant → refused before it runs
class MonthlyClose(Feature):
    def execute(self, dto):
        self.tenancy.active, self.tenancy.selected, self.tenancy.allowed
        criteria = self.tenancy.criteria(Invoice).merged_with(dto.criteria)   # the sub-criteria
        with self.tenancy.with_tenant("acme-sur"): ...
        with self.tenancy.with_tenants(["acme", "acme-sur"]): ...

@billing.app_service(CommandConsolidatedReport)
@tenancy.without_tenant(reason="consolidated report")   # across tenants, by name, logged
class ConsolidatedReport(ApplicationService): ...

@billing.app_service(CommandNightlyDunning)
@tenancy.each_tenant                             # once per active tenant of the catalog
class NightlyDunning(ApplicationService): ...
```

`with_tenant` / `without_tenant` are the names acts_as_tenant gives them; `each_tenant` is
spatie's `eachCurrent`. `self.tenancy.each(run)` collects an answer per tenant; a tenant that
fails never stops the others, and every failure is raised together.

**4. Who may name which tenant: memberships and the catalog.**

```python
tenancy = Tenancy(memberships=DatabaseMemberships(db, membership_table("membership", md)),
                  catalog=DatabaseTenantCatalog(db, tenant_table("tenant", md)))
```

On every entrance of a bus — HTTP, a queue, a remote bus, a cron — the tenant the caller names
is checked against the memberships of the subject (`user_id`, which auth verifies): one outside
is `TenantNotAllowed`; nothing named with one membership takes it; nothing named with several
is `TenantNotChosen` for a use case that requires one. A tenant the catalog holds
`SUSPENDED` or `OFFBOARDED` is never served.

## The rest of the framework, tenant-aware

| Piece | What tenancy gives it |
|---|---|
| Repository, `EntityReads`, `Edit`, `Import`, `Export` | the data filter of the store: nothing to override, nested Features and a plain session included |
| SQL statements | `with_loader_criteria` on every ORM SELECT/UPDATE/DELETE — `text()` is not filtered |
| Unique values | `unique_per_tenant("number")`; a reference that never crosses tenants: `tenant_foreign_key(...)` |
| Domain events | an event base that declares `tenant_id` is stamped and filtered; `event_table(..., *tenant_columns())`; the envelope carries the event's tenant, so a fact forwarded later opens its consumer in that tenant |
| External keys | `ExternalKey.scope` is the tenant: two tenants importing the same file never collide |
| Query cache | `bus.cache.queries` varies by tenant by default |
| Drafts, `bus.cache`, idempotency, pipeline runs | already owned by tenant (`Owner.BY_TENANT`) |
| Queues, remote buses, gRPC, HTTP | the tenant travels in the context (`sincpro-context`, baggage) and is validated at the entrance |
| Observability | `tenant` on every log, span, error and metric series (each tenant is a series); a crossing (`without_tenant`) is logged with its reason |
| Auth | `acting_tenant(identity)`: requirements, roles per tenant, entitlements and outgoing credentials read the tenant the identity acts in |

## Built-in use cases

```python
class QueryMyTenants(GetTenantContext): ...
class CommandAddMember(JoinTenant): ...

@accounts.feature([QueryMyTenants, CommandAddMember])
class Tenants(TenantUseCases): ...
```

`GetTenantContext` (a frontend's tenant selector), `ListTenants`, `PutTenant` (suspend,
reactivate, offboard), `ListTenantMembers`, `JoinTenant`, `LeaveTenant` — each overridable.

## Extending it

- **Ports.** `Memberships` (`tenants_of`) and `TenantCatalog` (`get`, `ids`) are the whole
  contract; an identity provider's organizations, an Odoo's `company_ids`, a control plane —
  each is an adapter of the project's own. The writes (`join`, `leave`, `put`) are optional.
- **The filter.** `TenantFilter` is a `DataFilter`; a rule of the project's own is a
  discriminator with `criteria=`, or a filter of its own beside it
  ([data filters](../persistence/data-filters.md)).
- **Your own multi-tenancy.** Use none of this: the context keys stay, and data filters take any
  rule.

## Why it is this shape

- **Isolation is not authorization.** Auth says who may run a use case; tenancy says which rows
  any use case may touch. Kept apart, each is replaceable alone, and the tenant filter applies
  even where no permission is declared.
- **The store, not the Feature, holds the filter.** Bases of the project's own (`TenantReads`,
  `ScopedReads`) left holes — a `LiteralSearch` not overridden, a Feature joined to another's
  unit of work. On the store, SQLAlchemy applies it to every statement (manifesto §25).
- **Derived, never inherited.** A field is enough; a mixin is a recipe. A project that names its
  field `company_id` declares it once.
- **Refused, never widened.** No tenant named is `TenantRequired`; reading across tenants is
  asked by name and logged.

What it does not do yet — schema or database per tenant, row-level security in Postgres as a
second line, a hierarchy of tenants (a company and its branches), settings per tenant, and
switching tenant within a session of the token issuer — is tracked in the repository's issues.
