---
name: sincpro-framework-tenancy
description: Multi-tenancy with sincpro_framework — the tenant context (active tenant_id, selected tenant_ids, allowed by Memberships), tenant discriminators derived from an aggregate's field (no base class required), TenantFilter as a data filter on the store (reads narrowed, records stamped, writes checked), Tenancy (on(bus), isolate(store), @tenancy.required, @tenancy.without_tenant, @tenancy.each_tenant, with_tenant, criteria), TenantCatalog and its lifecycle, built-in use cases, SQL recipes (tenant_columns, unique_per_tenant, tenant_foreign_key). Use whenever a task serves several customers/companies/organizations from one service, filters data by tenant, switches tenant, runs work per tenant, or adds a global query filter (DataFilter).
---

# sincpro-framework-tenancy

Tenancy partitions what a bounded context reads and writes by tenant. **Opt-in and standalone**:
a bus without it reads `tenant_id` from its context as always. **Isolation ≠ authorization**: auth
says who may run a use case; tenancy says which rows any use case may touch.

## The one-line setup

```python
from sincpro_framework.tenancy import Tenancy

tenancy = Tenancy()                       # any aggregate with a `tenant_id` field is partitioned
database = tenancy.isolate(Database(url)) # the tenant filter on the store
tenancy.on(billing)                       # resolved per call; `self.tenancy` in handlers
```

The Feature is written **as without tenancy**: `self.repository.search(...)`, `EntityReads`,
`Edit`, `Import` — every read is narrowed and every new record stamped by the store's filter.

## Rules

- **Never filter by tenant by hand** in a Feature (`Condition("tenant_id", dto.tenant_id)`): a
  value the caller sends is forgeable, and a forgotten read leaks. The store's filter does it.
- **Never take the tenant from a DTO.** It comes from the execution context, validated against
  `Memberships` when given.
- **No base class required.** A `tenant_id` field is enough (`TenantOwnedMixin` is a recipe);
  another field: `Tenancy(field="company_id")` or `tenancy.discriminator(X, field=...)`; a rule
  of the project's own: `tenancy.discriminator(X, criteria=lambda tenants: ...)`; a catalog
  every tenant reads: `tenancy.shared(Currency)`.
- **Reading across tenants is by name**: `@tenancy.without_tenant(reason=...)` or
  `with self.tenancy.without_tenant(reason=...)` — logged. No tenant named →
  `TenantRequired`, never "every tenant".
- **Work per tenant**: `@tenancy.each_tenant` on a use case that answers nothing (a cron), or
  `self.tenancy.each(run)` for an answer per tenant.
- **A subset of the caller's tenants** is a condition in its own `Criteria` — AND-ed, it only
  narrows.
- **Tables**: `*tenant_columns()`, `unique_per_tenant("number")`, `tenant_foreign_key(...)`;
  events: an event base declaring `tenant_id` + `event_table(..., *tenant_columns())`.
- **Global query filters of any kind** are `DataFilter`s on the store
  (`Database(filters=...)`, `MemoryRepository(filters=...)`, `DataFilters.disabled(name)`).

## Read

`docs/tenancy/README.md` (the component), `docs/persistence/data-filters.md` (the mechanism).
