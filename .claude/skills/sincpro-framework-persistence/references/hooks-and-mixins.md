# Hooks and mixins

A hook runs **inside the write**, on one aggregate: it validates, computes or refuses. It has what a
Feature of its bus has — other repositories, `self.bus`, `self.context` — but a write back through
the repository that fired it is refused rather than left to recurse.

Full depth: `docs/persistence/hooks.md` and `docs/persistence/lifecycle.md`.

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

In a project the collection lives in a package — `billing_hooks = Hooks()` in
`services/hooks/__init__.py`, one hook per module beside it — and `Hooks()` imports every module of
that package the first time it is read, so no hook is forgotten. `Hooks(None)` is a hand-filled
collection. Register it in `dependencies.py` like any other dependency: `billing_hooks.inject(framework)`.

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

`on(Invoice)` selects by `isinstance`, so a hook on a base class covers its subclasses, and
`on(object)` runs for every aggregate (an audit, a log).

## Mixins

| Mixin | Columns | Effect |
|---|---|---|
| `ArchivableMixin` | `archive_columns()` | `archive()` hides; reads skip archived unless asked |
| `AuditedMixin` | `audit_columns()` | `created_by`/`updated_by`, stamped at `before_flush` for every write path |
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
