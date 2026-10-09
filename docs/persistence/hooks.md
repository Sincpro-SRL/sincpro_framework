# Hooks: what a project puts around its own aggregates

A hook runs **inside the write**, on one aggregate: it validates, computes or refuses. It is a
class, registered for its aggregate the way a Feature is registered for its Command.

```python
from dataclasses import dataclass
from typing import TypedDict

from sincpro_framework import UseFramework
from sincpro_framework.ddd import ContractViolation, Entity, Hook, Hooks, MemoryRepository


@dataclass
class Invoice(Entity):
    total: int = 0


@dataclass
class CreditNote(Entity):
    total: int = 0


class BillingClient:
    def allows(self, total: int) -> bool:
        return total < 1_000


class DependencyContextType:  # what the bounded context registers, declared once
    billing: BillingClient
    trail: list[str]


class BillingContext(TypedDict, total=False):  # what a request carries, declared once
    user_id: str


class BillingHook(Hook[BillingContext], DependencyContextType):
    """The bounded context's base hook, typed as its Feature is: `self.<dependency>` from
    `DependencyContextType`, `self.context` from `Hook[BillingContext]`, and `self.bus`."""


billing = UseFramework("billing", log_after_execution=False)
billing.add_dependency("billing", BillingClient())
billing.add_dependency("trail", [])

billing_hooks = Hooks(None).inject(billing)


@billing_hooks.on(Invoice)
class InvoiceMustBalance(BillingHook):
    def before_save(self, invoice: Invoice) -> None:
        if not self.billing.allows(invoice.total):
            raise ContractViolation(f"billing refused {invoice.total}")
        self.trail.append("balances")


repository = MemoryRepository(hooks=billing_hooks)
repository.save(Invoice(total=100))
assert billing.deps.trail == ["balances"]
```

In a project the pieces live apart — the collection in the hooks package, one hook per module,
the wiring in the composition root:

```text
services/hooks/__init__.py     billing_hooks = Hooks()          walks this package on first read
services/hooks/invoices.py     from my_context import Hook       as a Feature imports Feature
                               @billing_hooks.on(Invoice) class InvoiceMustBalance(Hook)
framework.py                   class Hook(_Hook[BillingContext], DependencyContextType)
dependencies.py                billing_hooks.inject(billing)
                               repository = Repository(database, billing_hooks)
```

**Lazy, the way the bus is.** Building the repository reads nothing: the collection is read,
and its package walked, the first time a moment fires — when every module of the bounded context
has loaded. So the repository may be built wherever the project builds its dependencies, and a
hook imports its base from the context's package; the order modules are imported in does not
matter. `tests/ddd/repositories/test_hooks_in_a_bounded_context.py` writes such a context to disk
and imports it three ways.

## Why this exists

A bounded context owns rules about its aggregates that no single use case owns: an invoice has
to balance, a new one is numbered, a fact is written once. Written inside each Feature, they
are repeated and eventually forgotten in one of them; written as a hook, they run for every
write that goes through the repository.

An ERP adds a second need. A core ships its hooks, and a client project has to change one of
them — replace it, run something before it, add a check on top of it, switch it off — without
editing the core. As both grow, **the order they run in must stay predictable**, and there must
be **one way to write a hook**, or every extension multiplies what a reader has to know.

## What it guarantees

1. **One form.** A hook is a class registered with `@hooks.on(...)`. Its moments are the
   methods it implements; there is no second way to write one.
2. **By reference, never by name.** Every hook, replacement, constraint and switch-off names a
   class, never text — a typo is a Python error, not a hook that silently never runs.
3. **One order, always the same**, across hooks, interceptors and error handlers: `before` /
   `after`, then `sequence`, then registration. A circle is refused, naming it.
4. **A project changes a core without touching it**: `replaces=`, `extends=`, `without()`,
   `combined_with()`.
5. **A hook has what a Feature of its bus has**: `self.<dependency>` (another repository, a
   client), `self.context` (the request in play), and `self.bus` to execute a Command or a Query.
6. **Open by default.** Only what cannot work is refused, where it is declared: a circle, an
   `extends=` without a subclass, a hook with no moment, a hook registered too late. Anything
   that works, only not as said, is a warning in the log, and it runs.
7. **Both stores fire the same moments**, which `tests/orm/test_lifecycle_parity.py` proves by
   running one script against each.
8. **The one loop is refused**: a write back through the repository that fired the hook —
   directly or through a Command it executes — would fire the hook again, forever.

## The moments

A moment is a method the hook implements. Symmetric: whatever the store is about to do, there is
a before and an after.

| Operation | before | after |
|---|---|---|
| a write, either kind | `before_save` | `after_save` |
| …one never stored | `before_create` | `after_create` |
| …one that was | `before_update` | `after_update` |
| putting away | `before_archive` | `after_archive` |
| deleting | `before_remove` | `after_remove` |
| an aggregate handed back | — | `after_read(record)` |
| a page answered | — | `after_search(page)` |

- `save` fires for every write; `create` and `update` tell the same write apart, in that order:
  `before_save`, then `before_create` or `before_update`. An archive is a save, so
  `before_save` fires for it too.
- `after_read` fires once per aggregate handed back (`get`, `browse`, `search`, `fetch_all`,
  `stream`), and may answer a record to put in its place. It does not fire for `count`,
  `exists`, `pluck` or a grouping, which the engine answers in SQL without building anything.
- `after_search` fires once per page, handed the collection, and may answer one in its place.
- A batch runs every `before_*`, then the write: refused halfway, nothing is written and no
  `after_*` ran.
- There is no `before_read`: restricting what somebody may see is `narrowed()`. And
  `after_save` is **not** after the commit — `Database.after_commit` is.

## Which aggregates a hook is for

```python
documents_hooks = Hooks(None).inject(billing)


@documents_hooks.on([Invoice, CreditNote])  # each one listed
class AuditsDocuments(BillingHook):
    def after_save(self, record: Entity) -> None:
        self.trail.append(f"audited {type(record).__name__}")


billing.deps.trail.clear()
MemoryRepository(hooks=documents_hooks).save(CreditNote(total=5))
assert billing.deps.trail == ["audited CreditNote"]
```

A hook selects by `isinstance`, so one on a base class covers its subclasses, and
`@hooks.on(object)` covers every record — an audit, a log. What a hook is for is kept by the
collection, not written onto the class: the same class may be registered in two collections.

## Dependencies, the request, and the lifetime

- **`self.<name>`** is what the bus registered with `add_dependency`, resolved when the hook
  reads it — so the collection may be given its bus before or after the repository is built,
  including the knot where the repository is itself a dependency of the bus. Declared on the
  context's `DependencyContextType`, the IDE knows its type.
- **`self.context`** is the request in play, read-only — `user_id`, `tenant_id`,
  `correlation_id` — and `{}` outside one. `context` is therefore never a dependency's name.
- **One instance per hook per repository**, built the first time one of its moments fires —
  never while the repository is being built, so a hook that cannot be built does not take the
  store down. It serves every moment and every request of that repository, like a Feature:
  deciding something in `before_save` and acting on it in `after_save` works within one write,
  but **request state lives in locals, never in `self`**.
- **`self.bus`** is the bus itself, to execute a Command or a Query as an ApplicationService
  does. It runs inside the write, in its transaction; only a write back through the repository
  that fired the hook is refused. `context` and `bus` are therefore never a dependency's name.
- A unit of work (`context()`) and a narrowing (`narrowed()`) are the same store seen
  differently: they run the hooks of the repository they came from, with the same instances.

Another repository, the bus and the request, from one hook:

```python
from sincpro_framework import DataTransferObject, Feature


@dataclass
class Customer(Entity):
    credit_limit: int = 0


@dataclass
class SalesOrder(Entity):
    customer_id: str = ""
    total: int = 0
    currency: str = "BOB"


class QueryExchangeRate(DataTransferObject):
    currency: str


class ResponseExchangeRate(DataTransferObject):
    rate: int


class SalesDependencies:
    customers: MemoryRepository


class SalesHook(Hook, SalesDependencies):
    """The sales context's base hook."""


sales = UseFramework("sales", log_after_execution=False)
sales.add_dependency("customers", MemoryRepository(Customer(id="c1", credit_limit=1_000)))


@sales.feature(QueryExchangeRate)
class ExchangeRate(Feature):
    def execute(self, dto: QueryExchangeRate) -> ResponseExchangeRate:
        return ResponseExchangeRate(rate=7 if dto.currency == "USD" else 1)


sales_hooks = Hooks(None).inject(sales)


@sales_hooks.on(SalesOrder)
class RespectsTheCreditLimit(SalesHook):
    def before_save(self, order: SalesOrder) -> None:
        customer = self.customers.get(Customer, order.customer_id)  # another repository
        rate = self.bus(QueryExchangeRate(currency=order.currency), ResponseExchangeRate).rate
        if customer is None or order.total * rate > customer.credit_limit:
            raise ContractViolation(f"over the credit limit, asked by {self.context.get('user_id')}")


orders = MemoryRepository(hooks=sales_hooks)
with sales.context({"user_id": "ana"}):
    orders.save(SalesOrder(customer_id="c1", total=900))  # 900 BOB: within the limit
    try:
        orders.save(SalesOrder(customer_id="c1", total=200, currency="USD"))  # 1 400 BOB
        raise AssertionError("over the limit")
    except ContractViolation as refused:
        assert "asked by ana" in str(refused)
```

## The order several hooks run in

One algorithm for every extension point of the framework — hooks, interceptors, error
handlers — in `sincpro_framework.ordering`:

1. **`before=` and `after=`** are hard constraints, by reference.
2. **`sequence=`**, lower first — like an Odoo `sequence` — 10 when not said. What must run
   before an item takes that item's sequence when it is lower, so "just before the early one"
   is early too, never stuck behind everything later.
3. **The order they were registered** decides the rest — decorated, or walked by `load()`.
4. A circle of `before`/`after` is refused, naming it; a constraint on a hook that is not there
   (switched off, say) is no constraint.

```python
ordering = Hooks(None)


@ordering.on(Invoice, sequence=20)
class Late(Hook):
    def before_save(self, invoice: Invoice) -> None: ...


@ordering.on(Invoice)
class Ordinary(Hook):
    def before_save(self, invoice: Invoice) -> None: ...


@ordering.on(Invoice, sequence=5)
class Early(Hook):
    def before_save(self, invoice: Invoice) -> None: ...


@ordering.on(Invoice, sequence=99, before=Early)
class FirstOfAll(Hook):
    def before_save(self, invoice: Invoice) -> None: ...


assert [hook.__name__ for hook in ordering] == ["FirstOfAll", "Early", "Ordinary", "Late"]
```

`FirstOfAll` says 99 but must run before `Early` (5), so it takes 5 and goes first; the rest
follow by sequence.

## Replacing, extending, switching off

Each intention has one word:

| | the original | the new class | `super()` |
|---|---|---|---|
| `extends=Original` | stays part of it | a subclass — refused otherwise, `super()` could not run | runs the original, where and whether you call it |
| `replaces=Original` | is gone — every moment of it | any class; a subclass works, with a warning that it is `extends` | nothing to call, unless it subclasses |
| `without(Original)` | is gone | — | — |

Both `extends` and `replaces` take the place of the one they name — its position, its sequence,
its constraints — so nothing else moves.

A core publishes its collection; a project changes it without touching it:

```python
core_hooks = Hooks(None).inject(billing)


@core_hooks.on(Invoice)
class Numbers(BillingHook):
    def before_save(self, invoice: Invoice) -> None:
        self.trail.append("numbers")

    def after_save(self, invoice: Invoice) -> None:
        self.trail.append("announces")


@core_hooks.on(Invoice)
class Audits(BillingHook):
    def after_save(self, invoice: Invoice) -> None:
        self.trail.append("audits")


def saved_with(hooks: Hooks, invoice: Invoice) -> list[str]:
    billing.deps.trail.clear()
    MemoryRepository(hooks=hooks).save(invoice)
    return list(billing.deps.trail)


extending = Hooks(None)


@extending.on(Invoice, extends=Numbers)
class NumbersWhenPositive(Numbers):
    def before_save(self, invoice: Invoice) -> None:
        self.trail.append("checks first")
        if invoice.total > 0:
            super().before_save(invoice)


extended = core_hooks.combined_with(extending)
assert saved_with(extended, Invoice(total=5)) == ["checks first", "numbers", "announces", "audits"]
assert saved_with(extended, Invoice(total=0)) == ["checks first", "announces", "audits"]

replacing = Hooks(None)


@replacing.on(Invoice, replaces=Numbers)
class NumbersBySeries(BillingHook):
    def before_save(self, invoice: Invoice) -> None:
        self.trail.append("numbers by series")


replaced = core_hooks.combined_with(replacing)
assert saved_with(replaced, Invoice(total=5)) == ["numbers by series", "audits"]
assert replaced.replacements == {
    f"{__name__}.NumbersBySeries": (f"{__name__}.Numbers",)
}

assert saved_with(core_hooks.without(Audits), Invoice(total=5)) == ["numbers", "announces"]
assert saved_with(core_hooks, Invoice(total=5)) == ["numbers", "announces", "audits"]
```

- **An extension decides per moment.** What it does not override is inherited and still runs —
  `announces` above, in both saves.
- **A replacement drops every moment** of what it replaces: `announces` is gone, because
  `NumbersBySeries` did not write an `after_save`.
- `combined_with` puts the project's hooks after the core's and resolves replacements,
  extensions and constraints across both; `without` answers a copy. The core's collection is
  never changed — the last line above.
- `hooks.replacements` and `hooks.switched_off` say what was changed, as `module.Class`.

## Where the modules get imported

A decorator only runs if its module was imported, and a hook in a file nobody imported never
registers. So a collection walks its own package, once, the first time a moment fires on a
repository that has it — or `list(hooks)` is asked:

```text
Hooks()                          walks the module that built it — a package's __init__ is the package
Hooks("myapp.services.hooks")    walks that package, and only that one
Hooks(None)                      walks nothing; filled by hand
```

Written in an ordinary module, `Hooks()` walks that module, not the package around it. After the
walk the collection is closed: a hook registered later would never run, so it is refused.
`repr()` never walks, so printing one while debugging imports nothing. Being lazy, a mistake in
the collection — a circle — surfaces at the first save; `assert list(billing_hooks)` in a test
reads it at once, for a suite that wants it at startup.

## How it runs: the chain

Each repository runs its collection through a `HookChain`, compiled the first time a moment
fires — never while the repository is built:

1. The collection's hooks, in the order above (its package walked, if not yet).
2. For each moment, the hooks that implement it and the aggregates they are for.
3. At each moment of a read or a write, every hook whose aggregate the record is runs, in that
   order, on its one instance — built the first time it fires.
4. Final: the store's own hook closes the moment (change tracking, for one), so it sees the
   aggregate as the project's hooks left it.

A unit of work and a narrowing run the chain of the repository they came from.

## What is refused, and what is only said

Refused only what cannot work — a wiring mistake is `ExtensionRefused`, raised where it is
declared; a hook's own refusal of a record is the domain's `ContractViolation`:

| Cannot work | Refused |
|---|---|
| hooks that must run before one another in a circle — no order exists | `ExtensionRefused`, at the first read or write |
| `extends=X` on a class that is not a subclass of `X` — `super()` would fail | `ExtensionRefused`, at `on(...)` |
| a hook that implements none of the moments — it would never run | `ExtensionRefused`, at `on(...)` |
| a hook registered after a repository used the collection — it would never run | `ExtensionRefused`, at `on(...)` |
| a collection or a hook passed where the memory store's records go — nothing would run | `ContractViolation`, when the repository is built |
| `self.<name>` or `self.bus` with no bus given | `DependencyNotRegistered`, when the hook reads it |
| a write back through the repository that fired the hook, even via `context()`, `narrowed()` or a Command | `ContractViolation`, when it fires |

Everything else works, and is said in the log as a warning:

| Works, only not as said | What happens |
|---|---|
| `replaces=X`, and `X` is not registered — a core not installed | it runs as a hook of its own |
| `replaces=X`, and `X` was already replaced by `Y` | it replaces `Y` |
| `without(X)`, and `X` is not registered | nothing to switch off |
| `replaces=X` on a subclass of `X` | it runs; its `super()` still runs `X` — that is `extends` |
| `replaces=` and `extends=` together | it extends |
| a replacement that covers other aggregates than what it replaces | it covers its own |

```python
from sincpro_framework.exceptions import ExtensionRefused

refusals = Hooks(None)
try:

    @refusals.on(Invoice, extends=Numbers)
    class NotASubclass(BillingHook):
        def before_save(self, invoice: Invoice) -> None: ...

    raise AssertionError("extends needs a subclass")
except ExtensionRefused as refused:
    assert "has to be a subclass" in str(refused)
```

## Both stores

```text
Repository(database, billing_hooks)          SQLAlchemy: the collection is the second argument
MemoryRepository(hooks=billing_hooks)        memory: keyword — its positional arguments are records
```

## What was looked at, and what was taken

| Where | What it does | Taken, or not |
|---|---|---|
| **Odoo** `_inherit` + `super()`, `sequence` | a module extends a model by subclassing it; lower `sequence` first | taken: `extends=` is a subclass that runs the original through `super()`; `sequence` with the same meaning and default |
| **systemd** `Before=` / `After=` | ordering constraints that name units; one on a unit that is absent is ignored | taken: `before=` / `after=`, and an absent target is no constraint, so an extension survives its neighbour being switched off |
| **Gradle** `mustRunAfter`, **WordPress** priorities | hard constraints over soft priorities | taken: constraints first, then `sequence`, then registration |
| **Kahn's algorithm** (1962) | a stable topological sort that reports a cycle | taken: the sort, with a heap on (sequence, registration), and priority inheritance so a constraint never delays an early item |
| **Spring** `@Order`, **NestJS** interceptors | one ordering rule for every extension point | taken: hooks, interceptors and error handlers share `sincpro_framework.ordering` |
| **Django signals** | receivers as functions, registered by name or by sender, order by connection | not taken: two forms (function and class) doubled what a reader must know, and signals have no replace or extend |
| **Rails callbacks** | `before_save :method_name` — names as symbols | not taken: a name written as text fails silently on a typo; everything here is by reference |
| a global registry | hooks discovered wherever they are | not taken: a collection is an object somebody made and passes, so two contexts never reach each other's hooks |

## Decisions, and why

- **Only classes.** A function form was one more thing to learn and could not carry
  dependencies or several moments; a class does both, and reads like a Feature.
- **Open by default: refuse only what cannot work.** A project, or an agent writing one, must
  not be stopped by a rule about style; what works but surprises is a warning. A `replaces` on a
  subclass works, so it is said, not refused — `extends` is the word for it.
- **A hook has what a Feature has, the bus included.** Nothing stops it from reading another
  repository or executing a Command; only the one loop — writing back through the repository
  that fired it — is refused.
- **What a hook is for belongs to the collection**, not to the class — so registering a class
  never changes it, and one class can serve two collections.
- **The bus is the one door** (`inject(bus)`): dependencies and context from the same place a
  Feature reads them, read when used, so the wiring order never matters.
- **Lazy, like the bus**: nothing is read while a repository is built — the collection at the
  first moment, each instance at its first fire — so the order of imports never matters, and a
  store never fails to build because of a hook.
