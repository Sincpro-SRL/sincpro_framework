# Tenancy — many customers, one service, none seeing another

`sincpro_framework.tenancy` decides, use case by use case, which tenant's data each one reads and
writes: a customer, a company, an organization. It is opt-in and stands alone — a bus without it
reads `tenant_id` from its context as always — and the database knows nothing of tenants.

## What it is

| Idea | What it says | Where it lives |
|---|---|---|
| **Tenant context** | the tenants an execution works in | the execution context: `tenant_id` (active), `tenant_ids` (selected); allowed by `Memberships` |
| **Tenant discriminator** | the field that partitions an aggregate | declared: `tenancy.discriminator(Invoice)`, or the `TenantOwnedMixin` recipe |
| **Isolation** | reads narrowed, new records stamped, writes outside *not found* | the execution context, applied by the use case that asks for it |

| Set | Key | Meaning | Who says it |
|---|---|---|---|
| active | `tenant_id` | where a write goes | the caller, validated |
| selected | `tenant_ids` | what a read sees (`IN`) | the caller, validated |
| allowed | — | what the caller may select | `Memberships` — or anything, for whom `cross_tenant` says |

## The use cases, and their doors

Every behaviour is **one use case** in `tenancy/services`. A decorator declares it for a whole
use case of the bus; a `with` runs it for a block; `self.tenancy` reaches it from a handler. The
same use case is behind each door, so it answers the same way whichever door it came by.

| Use case | What it does | On a use case | In a block, or asked |
|---|---|---|---|
| **isolation** | reads narrowed to the selected tenants, new records stamped with the active one, a write outside *not found* | `@tenancy.isolated` | `with self.tenancy.isolated():` · `self.tenancy.repository(repo)` · `self.tenancy.criteria(X, c)` |
| **a tenant required** | refused before it runs without an active tenant; nothing filtered | `@tenancy.required` | `with self.tenancy.required():` |
| **crossing tenants** | the isolation off, by name, logged | `@tenancy.without_tenant(reason=...)` | `with self.tenancy.without_tenant(reason=...):` |
| **switching tenant** | another tenant, or a sub-selection, validated | — | `with self.tenancy.with_tenant(t):` · `with self.tenancy.with_tenants([...]):` |
| **every tenant** | once per active tenant of the catalog, each in its own | `@tenancy.each_tenant` | `self.tenancy.each(run)` |
| **who may** | asked without raising | — | `self.tenancy.allows(t)` · `.allowed` · `.active` · `.selected` · `.tenant()` |

What an isolated use case does with **no tenant** is said with `when_missing` — the tenancy side
of auth's `when_denied`:

| `WhenMissing` | No tenant named |
|---|---|
| `RAISE` (default) | `TenantRequired` |
| `SKIP` | the use case does not run and answers `None` — an optional step (a use case only: a block cannot skip its own body) |
| `SHARED` | only what belongs to no tenant is read; a new record belongs to none — a public catalog |

## How it is used

```python
# infrastructure/ — once
tenancy = Tenancy(memberships=..., catalog=..., cross_tenant=lambda: auth.allows(P.SUPPORT))
tenancy.discriminator(Invoice, field="company_id")
tenancy.shared(Currency)
tenancy.on(billing)                  # or on(billing, isolated=True): isolated unless said

# services/ — the use case decides
@billing.feature(CommandIssueInvoice)
@tenancy.isolated
class IssueInvoice(Feature): ...                     # stamped; reads only its tenant

@billing.feature([QueryGetInvoice, QueryListInvoices])
@tenancy.isolated
class InvoiceReads(EntityReads[Invoice]): ...         # Get, Search, Edit… nothing overridden

class OverdueInvoices(Feature):
    def execute(self, dto):
        rates = self.repository.fetch_all(ExchangeRate)        # not isolated
        with self.tenancy.isolated():                          # the tenant's only
            return self.repository.fetch_all(Invoice, overdue)

class InvoiceTotals(Feature):                                  # no magic: the condition in sight
    def execute(self, dto):
        return self.repository.measures(Invoice, self.tenancy.criteria(Invoice, dto.criteria), …)

class VoidInvoice(Feature):                                    # one repository isolated, not another
    def execute(self, dto):
        invoices = self.tenancy.repository(self.repository)
        invoice = invoices.get(Invoice, dto.id)                # another tenant's → None
        ...

@tenancy.without_tenant(reason="consolidated report")         # auth decides who may
class ConsolidatedReport(ApplicationService): ...

@tenancy.each_tenant
@tenancy.isolated
class NightlyDunning(ApplicationService): ...                 # once per tenant, each isolated
```

## The rules

| Rule | What it means |
|---|---|
| **The nearest declaration wins** | an isolated use case calling one `without_tenant` → that one reads every tenant; a crossing one calling an isolated one → that one is isolated |
| **No declaration inherits** | a use case that declares nothing, called inside an isolated one, is isolated; called alone, tenancy does not touch it — on a bus `isolated=True`, the use cases nobody wrapped are isolated |
| **The tenant travels; the isolation does not** | another service, a queue, a consumer of events receives `tenant_id`, validated at its entrance; whether it isolates is its own use case's declaration |
| **Threads and tasks inherit it** | `ContextExecutor`, `ContextThread`, `aio` copy the context; a bare `threading.Thread` does not |
| **Hooks and interceptors see it** | they run inside the use case |
| **What is mine and what is not** | another tenant's record is *not found* — never told apart from one that does not exist |
| **Declared, never by convention** | a `tenant_id` nobody declared is a field like any other |

## The rest of the framework

| Piece | With tenancy |
|---|---|
| Repository, `EntityReads`, `Edit`, `Import`, `Export`, a nested Feature, a plain session | read through the isolation the use case asked for ([data filters](../persistence/data-filters.md)) — `text()` is not filtered |
| Tables | `tenant_columns()`, `unique_per_tenant("number")`, `tenant_foreign_key(...)` — Alembic picks them up |
| Domain events | an event base that declares `tenant_id` is stamped and filtered; `event_table(..., *tenant_columns())`; the envelope carries the event's tenant |
| External keys | `ExternalKey.scope` is the tenant: two tenants importing the same file never collide |
| Query cache, drafts, `bus.cache`, idempotency, pipeline runs | owned by tenant |
| Queues, remote buses, gRPC, HTTP | the tenant travels in the context and is validated at the entrance |
| Observability | `tenant` on every log, span, error and metric series; a crossing is logged with its reason |
| Auth | `acting_tenant(identity)` for requirements, roles per tenant, entitlements, outgoing credentials; `cross_tenant` is auth's answer, asked by tenancy |

## Built-in use cases

```python
class QueryMyTenants(GetTenantContext): ...
class CommandAddMember(JoinTenant): ...

@accounts.feature([QueryMyTenants, CommandAddMember])
class Tenants(TenantUseCases): ...
```

`GetTenantContext` (a frontend's tenant selector), `ListTenants`, `PutTenant` (suspend,
reactivate, offboard), `ListTenantMembers`, `JoinTenant`, `LeaveTenant`.

## Extending it

- **Ports.** `Memberships` (`tenants_of`) and `TenantCatalog` (`get`, `ids`) are the contract;
  `join`, `leave`, `put` are optional. In memory and on a database ship; an identity provider's
  organizations or an Odoo's `company_ids` are adapters of the project's own.
- **A rule of the project's own.** `tenancy.discriminator(Project, criteria=lambda tenants: ...)`.
- **Below it.** `TenantFilter` is a `DataFilter`; `DataFilters.applied(...)` applies any filter
  to a block, and a store may hold filters of its own.
- **A multi-tenancy of your own.** Use none of this: the context keys stay.

## Why it is this shape

- **Isolation is not authorization.** Auth says who may run a use case; tenancy which rows it
  touches. `cross_tenant` is where they meet, and auth answers it.
- **The use case decides, the framework executes.** The database holds no tenant rule;
  SQLAlchemy applies the isolation a use case asked for to every statement it runs
  (`with_loader_criteria`, manifesto §25) — nested calls and plain sessions included.
- **One use case per behaviour, many doors.** A decorator, a block and `self.tenancy` never
  disagree, because they are the same code.
- **Refused, never widened.** No tenant named is `TenantRequired`; reading across tenants is
  asked by name and logged.

Not yet: schema or database per tenant (#344), Postgres row-level security (#345), a hierarchy
of tenants (#346), settings per tenant (#347), switching tenant within a session (#348).
