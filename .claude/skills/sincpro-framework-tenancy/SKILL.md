---
name: sincpro-framework-tenancy
description: Multi-tenancy with sincpro_framework — the tenant context (active tenant_id, selected tenant_ids, allowed by Memberships), tenant discriminators derived from an aggregate's field (no base class required), isolation declared by the use case (@tenancy.isolated, with self.tenancy.isolated(), self.tenancy.repository, self.tenancy.criteria, WhenMissing RAISE/SKIP/SHARED), @tenancy.required, @tenancy.without_tenant, @tenancy.each_tenant, with_tenant, allows, cross_tenant, TenantCatalog and its lifecycle, built-in use cases, SQL recipes (tenant_columns, unique_per_tenant, tenant_foreign_key). Use whenever a task serves several customers/companies/organizations from one service, filters data by tenant, switches tenant, runs work per tenant, or adds a global query filter (DataFilter).
---

# sincpro-framework-tenancy

Tenancy partitions what a bounded context reads and writes by tenant. **Opt-in and standalone**:
a bus without it reads `tenant_id` from its context as always. **Isolation ≠ authorization**: auth
says who may run a use case; tenancy says which rows any use case may touch.

## Setup — once, in infrastructure/

```python
from sincpro_framework.tenancy import Tenancy, WhenMissing

tenancy = Tenancy(memberships=..., catalog=..., cross_tenant=lambda: auth.allows(P.SUPPORT))
tenancy.discriminator(Invoice, field="company_id")   # declared — never by convention
tenancy.on(billing)                                   # or on(billing, isolated=True)
```

The database knows nothing of tenants. **The use case decides**, through one of the doors to
the same use case of `tenancy/services`:

| Need | Door |
|---|---|
| the whole use case in its tenant | `@tenancy.isolated` (`when_missing=WhenMissing.SKIP` / `SHARED`) |
| a block | `with self.tenancy.isolated():` |
| one repository | `self.tenancy.repository(self.repository)` |
| the condition in sight | `self.tenancy.criteria(Invoice, dto.criteria)` |
| a tenant needed, nothing filtered | `@tenancy.required` |
| across tenants | `@tenancy.without_tenant(reason=...)` (+ `@auth.requires`) |
| another tenant / a sub-selection | `with self.tenancy.with_tenant(t)` / `with_tenants([...])` |
| once per tenant | `@tenancy.each_tenant` (answers nothing) · `self.tenancy.each(run)` |
| may the caller? | `self.tenancy.allows(t)` |

## Rules

- **Never filter by tenant by hand** with a value of the DTO: it is forgeable, and a forgotten
  read leaks. Declare `@tenancy.isolated`, or use `self.tenancy.criteria`.
- **The nearest declaration wins; no declaration inherits.** A nested use case runs isolated
  inside an isolated one.
- **The tenant travels, the isolation does not.** Each bus declares its own.
- **Another tenant's record is not found** (`AggregateNotFound`), never "forbidden".
- **Tables**: `*tenant_columns()`, `unique_per_tenant("number")`, `tenant_foreign_key(...)`.
- **Global query filters of any kind** are `DataFilter`s: `DataFilters.applied(...)` for a block,
  `Database(filters=...)` for every read of a store, `DataFilters.disabled(name)`.

## Read

`docs/tenancy/README.md` (the component), `docs/persistence/data-filters.md` (the mechanism).
