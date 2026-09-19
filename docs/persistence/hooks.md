# Hooks: what a project puts around its own aggregates

A rule runs inside the write, on one aggregate, and it validates, computes or refuses. It does
not publish, does not call a bus, and does not write — the last one is refused rather than left
to recurse.

```python
# services/hooks/__init__.py
from sincpro_framework.ddd.repositories import Hooks

billing_hooks = Hooks()

# services/hooks/invoices.py
from sincpro_framework.ddd.repositories import Hook
from . import billing_hooks

@billing_hooks
class InvoiceMustBalance(Hook):
    entity = Invoice

    def before_save(self, invoice: Invoice) -> None:
        if invoice.total != sum(line.amount for line in invoice.lines):
            raise ContractViolation("an invoice has to balance")

# the composition root
repository = Repository(database, billing_hooks)
```

## Two shapes, one mechanism

**A class**, when the rule has a name worth writing down or needs dependencies:

```python
class InvoiceMustBalance(Hook):
    entity = Invoice
    def before_save(self, invoice): ...
```

**A `Rule`**, when it is a plain function and lives beside the wiring:

```python
Rule(entity=Invoice, before_save=check_totals)
Rule(entity=Invoice, before_save=[check_totals, check_tax])   # one or several, in order
```

Both end in the same place: a hook that declares `entity` *is* a rule, and the repository
compiles it into one.

## The moments

Symmetric: whatever the store is about to do, there is a before and an after.

| Operation | before | after |
|---|---|---|
| a write, either kind | `before_save` | `after_save` |
| …one never stored | `before_create` | `after_create` |
| …one that was | `before_update` | `after_update` |
| putting away | `before_archive` | `after_archive` |
| deleting | `before_remove` | `after_remove` |
| a page answered | — | `after_search(collection)` |
| an aggregate handed back | — | `after_read(record)` |

`save` fires for every write; `create` and `update` tell the same write apart, so a rule that
cares about only one of them does not have to ask. **They fire in that order**: `before_save`,
then `before_create` or `before_update`.

**An archive is a save**, so `before_save` fires for it too; `before_archive` is the pair that
fires only for archiving.

**`after_read` fires once per aggregate handed back** — `get`, `browse`, `search`, `fetch_all`,
`stream`. Not for `count`, `exists`, `pluck` or a grouping, which the engine answers in SQL
without building anything, and which therefore would have fired in a test and not in production.

**`after_search` fires once for the page**, not once per row, and is handed the collection. A
rule about the set rather than about an aggregate belongs there. A paged walk fires it once per
page: seven rows over pages of three is `[3, 3, 1]`, and `after_read` seven times in total.

There are no `before_read` or `before_search` — a read has no aggregate to hand one yet, and
restricting what somebody may see is `narrowed()`, which the store cannot be talked out of.
There is no `after_commit` or `after_rollback` here either; a transaction is the store's, and
`Database.after_flush` is where the engine exposes its own.

**Both stores fire all of this identically**, which `tests/orm/test_lifecycle_parity.py` proves
by running one script against each and comparing the answers.

## Which aggregates a hook is for

`entity` selects by `isinstance`, so a hook on a base class covers its subclasses.

```python
class Audits(Hook):
    entity = Invoice        # this aggregate and anything that inherits it
```

**Left alone, `entity` is `object`, which every record is** — so a hook that names no aggregate
runs for all of them. That is what an audit or a log wants, and there is no special case behind
it: `isinstance(record, object)` is simply always true.

Several hooks for one aggregate run in the order the collection holds them, which is the order
they were decorated — and with `load()`, the order `pkgutil` walks the package, which is
alphabetical by module filename.

## Dependencies, and the request behind them

A hook reads what `add_dependency` registered as attributes, the same way a `Feature` does:

```python
@billing_hooks
class ChecksWithBilling(Hook):
    billing: BillingClient       # declared for the IDE, resolved when the hook runs

    def before_save(self, invoice: Invoice) -> None:
        if not self.billing.allows(invoice.total):
            raise ContractViolation("billing refused it")
```

Two doors, and the nearer one wins:

```python
billing_hooks.inject(bus.deps)                  # names only
billing_hooks.inject(bus)                       # names, and self.context
repository = Repository(database, billing_hooks, deps=bus.deps)   # or here
```

**Resolved when the hook runs, not when it is built**, which is what makes the wiring order stop
mattering — including the knot a bus forces, where the repository is a dependency *of* the bus
and so the bus is built around it:

```python
repository = Repository(database, billing_hooks)
bus.add_dependency("repository", repository)
billing_hooks.inject(bus)                       # after, and it still works
```

`self.context` is what the request in play says, read-only, empty outside one and empty when the
collection was given `bus.deps` rather than the bus. **`context`, `entity` and `bind` are
therefore names a hook cannot use for a dependency** — the class answers first.

## Where the modules get imported

A decorator only runs if its module was imported, and a hook in a file nobody imported never
registers — no error, nothing happens.

```python
billing_hooks = Hooks()                       # walks the module it was written in
billing_hooks = Hooks("myapp.services.hooks") # walks that package, and only that one
billing_hooks = Hooks(None)                   # walks nothing; filled by hand
```

Written in a package's own `__init__.py`, `Hooks()` walks that package — which is the layout
this is for. Written in an ordinary module, it walks that module, not the package around it.

The walk happens the first time the collection is read, which is when a repository is built with
it. `repr()` never triggers it, so printing one while debugging imports nothing.

## What is refused, and where

| | |
|---|---|
| a hook that implements none of the moments | `ContractViolation`, **at wiring** |
| a hook in a `Rule` slot that answers neither that moment nor `__call__` | `ContractViolation`, at wiring |
| a collection passed where aggregates go — `MemoryRepository(billing_hooks)` | `ContractViolation`, at wiring |
| a hook that writes through the same repository | `ContractViolation`, when it fires |
| `self.<name>` that nobody registered | `DependencyNotRegistered`, naming what *is* registered |

**A hook that writes is refused, including through `context()` or `narrowed()`** — those hand
back a different `Repository` over the same store, and the guard is counted against the store.
Reading from inside a hook is fine and is the point of one. What needs to write to several
aggregates is a Feature.

## What belongs to the store instead

A hook runs for whoever goes through that repository. Two things do not, and cannot:

- **Who wrote it** (`AuditedMixin`) and **what changed** (`ChangeTrackingMixin`) are registered
  on the session, so they reach a use case holding a plain session, a script or a migration.
- `after_save` is **not** after the commit. Inside `context()` a save flushes and the block
  commits later, so announcing a fact from `after_save` announces one a rollback can still take
  back. What tells the world lives outside the write.

## Lifetimes

One instance per hook per store, built the first time it fires — never while the repository is
being built, so a hook whose `__init__` is expensive or fails cannot take the store down with
it. A hook that implements two moments is **one** object in both, so deciding something in
`before_save` and acting on it in `after_save` works.

A hook handed in already built is copied, so the caller's object is never bound behind their
back and two stores never share one.

## Both stores

```python
Repository(database, billing_hooks, deps=bus.deps)          # SQLAlchemy: hooks is positional #2
MemoryRepository(hooks=billing_hooks, deps=bus.deps)        # memory: keyword, records are positional
```

The positions differ because the memory store's first arguments are the records it is seeded
with. Passing a collection there positionally is refused rather than stored as a record.
