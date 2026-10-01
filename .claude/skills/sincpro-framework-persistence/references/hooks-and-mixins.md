# Hooks and mixins

A hook runs **inside the write**, on one aggregate: it validates, computes or refuses. It has what a
Feature of its bus has — other repositories, `self.bus`, `self.context` — but a write back through
the repository that fired it is refused rather than left to recurse.

In the framework repo, `docs/persistence/hooks.md` and `docs/persistence/lifecycle.md` go deeper;
this page is enough to use them.

A hook is for **one deployment's rule about one aggregate** (an invariant, a derived field, a
policy). What needs several aggregates is a Feature; what must happen after the commit is an
event published by the Feature, not a hook.

## A hook is a class registered for its aggregate

```python
from sincpro_framework.ddd import ContractViolation, Hook, Hooks


class Numbering:
    def __init__(self) -> None:
        self.last = 100

    def next(self) -> str:
        self.last += 1
        return f"F-{self.last}"


invoicing_hooks = Hooks(None)


@invoicing_hooks.on(Invoice)
class TotalIsPositive(Hook):
    def before_save(self, invoice: Invoice) -> None:
        if invoice.total <= 0:
            raise ContractViolation(f"invoice {invoice.number} has no total")


@invoicing_hooks.on(Invoice, after=(TotalIsPositive,))
class NumberNewInvoices(Hook):
    numbering: Numbering                    # a dependency of the bus the hooks are injected into

    def before_create(self, invoice: Invoice) -> None:
        if not invoice.number:
            invoice.number = self.numbering.next()


accounting = UseFramework("accounting")
accounting.add_dependency("numbering", Numbering())
invoicing_hooks.inject(accounting)
numbered = Repository(database, invoicing_hooks)
```

In a project the pieces live apart:

```text
services/hooks/__init__.py        billing_hooks = Hooks()       walks this package on first use
services/hooks/invoices.py        @billing_hooks.on(Invoice) class InvoiceMustBalance(BillingHook)
infrastructure/framework.py       class BillingHook(Hook[BillingContext], DependencyContextType)
infrastructure/dependencies.py    billing_hooks.inject(framework)
                                  framework.add_dependency("repository", Repository(database, billing_hooks))
```

| Built as | Walks |
|---|---|
| `Hooks()` | the module that built it — a package's `__init__.py` is the package; an ordinary module is only itself |
| `Hooks("myapp.billing.services.hooks")` | that package |
| `Hooks(None)` | nothing; filled by hand |

The walk happens lazily, the first time a moment fires, so import order does not matter. After it
the collection is closed: a hook registered later raises `ExtensionRefused`. A hook that
implements no moment, or `extends=X` on a class that is not a subclass of `X`, is refused at
`on(...)`. `assert list(billing_hooks)` in a test reads the collection at once.

`self.<name>` is any dependency of the injected bus, `self.context` the request in play (`{}`
outside one), `self.bus` the bus (to execute a Query). Without `inject(bus)` reading any of them
raises `DependencyNotRegistered`. One instance per hook per repository serves every request:
request state lives in locals, never on `self`.

## The moments, in order

| Operation | before | after |
|---|---|---|
| any write | `before_save` | `after_save` |
| …of a new aggregate | `before_create` | `after_create` |
| …of a stored one | `before_update` | `after_update` |
| `archive` | `before_archive` | `after_archive` |
| `remove` | `before_remove` | `after_remove` |
| a record read | — | `after_read` |
| a page answered | — | `after_search` |

`on(Invoice)` selects by `isinstance`, so a hook on a base class covers its subclasses,
`on([Invoice, CreditNote])` covers each, and `on(object)` runs for every aggregate (an audit, a
log). `archive` is a save: `before_archive`, then the save's moments (`before_save`, `before_update`, …
`after_update`), then `after_archive`. Every
`before_*` of a batch runs before the flush and every `after_*` after it; `after_*` still runs
**before the commit** inside `context()`.

Ordering and extension: `on(X, after=(Other,), before=(...), sequence=5)`,
`on(X, replaces=Other)` (takes its place), `on(X, extends=Other)` (a subclass; `super()` runs it),
`hooks.without(Other)` and `core_hooks.combined_with(client_hooks)` give new collections and leave
the original whole.

A hook that writes through the repository that fired it — directly, through `context()`,
`narrowed()` or a Command — raises `ContractViolation`: a rule validates, computes or refuses.

## Mixins

| Mixin | Columns | Effect |
|---|---|---|
| `ArchivableMixin` | `archive_columns()` | `archive()` hides; reads skip archived unless asked |
| `AuditedMixin` | `audit_columns()` | `created_by`/`updated_by`, stamped at every flush from `Database(actor=…)`; `None` without an actor |
| `ChangeTrackingMixin` | — | one `EntityUpdated` event per save (see domain-events skill) |

## Append-only, with a hook

Append-only is a discipline, not a store mode. A four-line hook enforces it:

```python
claims_hooks = Hooks(None)


@claims_hooks.on(Claim)
class WrittenOnce(Hook):
    def before_save(self, record: Claim) -> None:
        if not record.is_new:
            raise ContractViolation(f"{type(record).__name__} is written once and never replaced")


Repository(database, claims_hooks)
```
