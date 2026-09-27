# Runtime use cases — use cases stored as source, loaded while the service runs

`sincpro_framework.runtime_use_cases` keeps Commands, Responses and the Feature or
ApplicationService that answers them as data — the Python source of a module, in a table of
yours or in memory — and loads them onto the bus of a bounded context without a deploy. The source
is the only truth about a stored use case: it is written, reviewed and tested as any Python
module, and it answers through the same bus, interceptors and observability as the code.

A bus is not changed once built. `BusRegistry` builds a new generation of it — what the code
registers plus what is stored — checks it by building it, and swaps it in with one assignment. A
request runs on one generation to the end; a generation that is refused leaves the bus as it was.

It is opt-in and needs nothing beyond the bus. Every block on this page runs, in order, in
`tests/docs/test_persistence_guide.py`.

## The bus in code

The bus of the context, declared as always. The registry never builds it nor registers on it:
each generation starts from `billing.fresh()` — a new bus, not built, with everything the code
registered on this one, in order, and the same dependencies and observability:

```python
from decimal import Decimal

from sincpro_framework import DataTransferObject, Feature, UseFramework


class CommandComputeTax(DataTransferObject):
    amount: Decimal


class ResponseComputeTax(DataTransferObject):
    tax: Decimal


billing = UseFramework("billing", log_after_execution=False)


@billing.feature(CommandComputeTax)
class ComputeTax(Feature):
    def execute(self, dto: CommandComputeTax) -> ResponseComputeTax:
        return ResponseComputeTax(tax=dto.amount * Decimal("0.13"))
```

Whoever serves requests reads the bus from the registry — `registry.current` — not `billing`,
which never sees a stored use case.

## A stored use case

A `RuntimeUseCase` is a name and the source of a module with one Feature or ApplicationService;
the Command it answers is the one its `execute` declares. Its module is
`sincpro_runtime.<context>.<name>`, so its Commands are routed by that name — by MCP, RPC, a
queue, or `registry.execute`:

```python
from sincpro_framework.runtime_use_cases import BusRegistry, InMemoryUseCases, RuntimeUseCase

QUOTE = '''
from decimal import Decimal

from sincpro_framework import DataTransferObject, Feature


class CommandQuote(DataTransferObject):
    amount: Decimal


class ResponseQuote(DataTransferObject):
    total: Decimal


class Quote(Feature):
    def execute(self, dto: CommandQuote) -> ResponseQuote:
        return ResponseQuote(total=dto.amount * Decimal("1.13"))
'''

store = InMemoryUseCases()
store.save(RuntimeUseCase("quote", QUOTE))
registry = BusRegistry(billing, store)

quoted = registry.execute("sincpro_runtime.billing.quote.CommandQuote", {"amount": 100})
assert quoted.total == Decimal("113.00")
assert registry.current(CommandComputeTax(amount=Decimal("100")), ResponseComputeTax).tax == Decimal("13.00")
```

What decides a new generation is the content: a changed source, `active` or `replaces`. `version`
names the source in refusals and tracebacks, so a line points at the text that holds it.
The names a generation routes are its `current.dto_registry` keys; a stored Command goes by its
module and class, never by its class alone.

`registry.execute` builds the Command and executes it on one generation. A stored Command is a
new class in each generation, and a bus answers a class, so a Command built against one
generation is answered only by that generation's bus — reach stored use cases by name.

## A new version, swapped in whole

`check` builds the generation a draft would join and lets it go — before it is saved. `reload`
swaps in a generation with what the store holds, and answers `False`, with no build, when
nothing changed:

```python
from sincpro_framework.runtime_use_cases import UseCaseRefused

draft = RuntimeUseCase("quote", QUOTE.replace("1.13", "1.16"), version=2)
registry.check(draft)
store.save(draft)

before = registry.current
assert registry.reload()
assert registry.generation == 2
assert registry.execute("sincpro_runtime.billing.quote.CommandQuote", {"amount": 100}).total == Decimal("116.00")
assert not registry.reload()
answering = registry.current

try:
    registry.check(RuntimeUseCase("quote", "class Quote(Feature)\n", version=3))
except UseCaseRefused as refused:
    assert "quote v3" in str(refused) and "line 1" in str(refused)
assert registry.current is answering and registry.generation == 2
```

A request that read `before` finishes on it. What a stored use case gets wrong — its syntax, its
imports, its module body, a Command the code already answers — is refused in its name, and the
bus answering stays. `reload` raises `UseCaseRefused` the same way, and keeps raising while the
refused version is the one under its name: save a fixed version, or the last good source again,
and the next `reload` swaps it in. A traceback from a stored use case shows the line that failed, from
`<runtime billing.quote v2 #…>`. Poll `reload` from a cron, or call it after a save; with several
replicas, each one reloads its own.

## Calling the code, replacing the code

A stored use case imports the code's Commands as any module does — in a service, from their
module (`from billing.commands import CommandComputeTax`); here the code is this page,
`__name__`. An ApplicationService calls the code's Features through `self.feature_bus`, and a
stored handler `replaces=` one in code, by its `module.Class`, as a Feature in code does:

```python
CHECKOUT = f'''
from decimal import Decimal

from sincpro_framework import ApplicationService, DataTransferObject

from {__name__} import CommandComputeTax


class CommandCheckout(DataTransferObject):
    amount: Decimal


class ResponseCheckout(DataTransferObject):
    total: Decimal


class Checkout(ApplicationService):
    def execute(self, dto: CommandCheckout) -> ResponseCheckout:
        tax = self.feature_bus.execute(CommandComputeTax(amount=dto.amount)).tax
        return ResponseCheckout(total=dto.amount + tax)
'''

TAX_WITH_EXEMPTION = f'''
from decimal import Decimal

from sincpro_framework import Feature

from {__name__} import CommandComputeTax, ResponseComputeTax


class ComputeTaxWithExemption(Feature):
    def execute(self, dto: CommandComputeTax) -> ResponseComputeTax:
        rate = Decimal("0") if dto.amount < 100 else Decimal("0.13")
        return ResponseComputeTax(tax=dto.amount * rate)
'''

store.save(RuntimeUseCase("checkout", CHECKOUT))
store.save(RuntimeUseCase("tax", TAX_WITH_EXEMPTION, replaces=f"{__name__}.ComputeTax"))
registry.reload()

small = registry.execute("sincpro_runtime.billing.checkout.CommandCheckout", {"amount": 50})
assert small.total == Decimal("50")
```

Retiring a use case is saving it inactive; the next generation is built without it:

```python
store.save(RuntimeUseCase("tax", TAX_WITH_EXEMPTION, version=2, active=False, replaces=f"{__name__}.ComputeTax"))
registry.reload()
assert registry.execute("sincpro_runtime.billing.checkout.CommandCheckout", {"amount": 50}).total == Decimal("56.50")
```

## Where use cases are kept

`UseCaseStore` is the contract: `active()` answers every use case to load, in the order they were
first saved, and `save(use_case)` keeps it as the one under its name. The core ships
`InMemoryUseCases`; with the `[sqlalchemy]` extra, `SqlUseCases` keeps them in a table of the
context's database — what the replicas of a service share, so a use case saved by one is loaded
by every one on its next `reload`. The table is declared on the context's own `MetaData`, so its
migrations create and change it like any other of its tables:

```python
from sqlalchemy import MetaData

from sincpro_framework.orm import Database
from sincpro_framework.orm.runtime_use_cases import SqlUseCases, use_case_table

metadata = MetaData()                                   # the context's tables
use_cases = use_case_table(metadata)                    # "runtime_use_case"; name it per context
database = Database("sqlite:///billing.sqlite3")
metadata.create_all(database.engine)                    # a migration, in a service

one_replica = BusRegistry(billing, SqlUseCases(database, use_cases))
other_replica = BusRegistry(billing, SqlUseCases(database, use_cases))

one_replica.store.save(RuntimeUseCase("quote", QUOTE))
other_replica.reload()
assert other_replica.execute("sincpro_runtime.billing.quote.CommandQuote", {"amount": 100}).total == Decimal("113.00")
```

Any other storage implements the two methods:

```python
from sincpro_framework.runtime_use_cases import UseCaseStore


class DictUseCases(UseCaseStore):
    def __init__(self, *use_cases: RuntimeUseCase) -> None:
        self.kept = {use_case.name: use_case for use_case in use_cases}

    def active(self) -> list[RuntimeUseCase]:
        return [use_case for use_case in self.kept.values() if use_case.active]

    def save(self, use_case: RuntimeUseCase) -> None:
        self.kept[use_case.name] = use_case


listed = BusRegistry(billing, DictUseCases(RuntimeUseCase("quote", QUOTE)))
assert listed.execute("sincpro_runtime.billing.quote.CommandQuote", {"amount": 10}).total == Decimal("11.30")
```

Stored source runs with the service's permissions: whoever can save a use case can run code in
it. Keep saving behind the same review a deploy has — `check` catches what does not load, not
what should not run.

## Reference

| | |
|---|---|
| `RuntimeUseCase(name, source, version=1, active=True, replaces=None)` | a use case as data; `checksum` of its source |
| `BusRegistry(bus, store)` | the first generation, `bus.fresh()` plus the store, built now |
| `registry.current` / `registry.generation` | the bus answering / how many were swapped in |
| `registry.reload()` | a new generation from the store; `False` when nothing changed |
| `registry.check(use_case)` | the generation it would join, built and let go |
| `registry.execute(dto_name, payload)` | a Command built and executed on one generation |
| `UseCaseStore` / `InMemoryUseCases` | where use cases are kept / in memory |
| `SqlUseCases(database, table)` / `use_case_table(metadata, name)` | in a table of the context's database (`[sqlalchemy]`) |
| `UseCaseRefused` | a use case that cannot be loaded, named with its version |
| `UseFramework.fresh()` | a new bus of the context, not built, with everything registered on this one |
| `UseFramework.handler_of(dto)` | the handler registered now for a Command |
