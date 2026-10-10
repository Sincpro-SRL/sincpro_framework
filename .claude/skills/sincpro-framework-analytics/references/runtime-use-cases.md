# Runtime use cases

Deep doc in the framework repo: `docs/runtime_use_cases/README.md`, PRD_06 and PRD_07 (every
block there runs as a test). This page stands alone.

`sincpro_framework.runtime.runtime_use_cases` keeps Commands, Responses and the Feature or ApplicationService
that answers them as **data** — the source of a module, in a table or in memory — and loads them onto
the bus of a bounded context without a deploy. The source is the only truth, written and reviewed as
any Python module; it answers through the same bus, interceptors and observability as the code.

## The bus in code

The registry never builds the context's bus nor registers on it: each generation starts from
`billing.fresh()` — a new bus, not built, with everything the code registered on this one, in order,
and the same dependencies and observability. **Whoever serves requests reads `registry.current`, not
`billing`**, which never sees a stored use case.

## A stored use case

```python
from sincpro_framework.runtime.runtime_use_cases import BusRegistry, InMemoryUseCases, RuntimeUseCase

store = InMemoryUseCases()
store.save(RuntimeUseCase(name="quote", source=QUOTE))
registry = BusRegistry(billing, store)

quoted = registry.execute("billing.CommandQuote", {"amount": 100})
```

- A `RuntimeUseCase` is a name and the source of a module with one Feature/ApplicationService; the
  Command it answers is the one its `execute` declares (`execute(self, dto: CommandX)` — the
  annotation is required). Its module is `sincpro_runtime.<context>.<name>`; its Commands are
  routed by their identity, `<context>.<Class>`, as the ones in code are.
- **An entrypoint holds the bus object it was given.** A gateway (MCP, RPC, gRPC, REST, a queue)
  built on `billing` never sees a stored use case; built on `registry.current`, it keeps answering
  that generation after a reload. To serve live generations, resolve `registry.current` per
  request — e.g. a handler that calls `registry.execute(dto_name, payload)`.
- **Reach stored use cases by name.** A stored Command is a new class in each generation, and a bus
  answers a class — a Command built against one generation is answered only by that generation's bus.
- The registry reads its store and builds its first generation on **first use** (`current`, `execute`,
  `reload`, `put`), not when it is made.

## A new version, swapped in whole

```python
registry.check(draft)                 # builds the generation a draft would join, then lets it go
store.save(draft)
registry.reload()                     # False when nothing changed
registry.put(RuntimeUseCase(...))     # check + save + swap in, atomically
```

- What decides a new generation is the content (source, `active`, `replaces`). `version` names the
  source in refusals and tracebacks.
- A request that read a generation finishes on it. What a stored use case gets wrong — syntax,
  imports, a Command the code already answers — is refused in its name (`UseCaseRefused`), and the bus
  answering stays.
- A stored use case whose Command the code already answers is refused unless it says
  `replaces="module.Class"`; `replaces` naming the wrong class, or a Feature replacing an
  ApplicationService, is refused too.
- A stored source imports the code, so a refactor can break a stored use case that nothing reloads.
  `check_all()` loads every active one against the code as it is now — run it in CI.
- A stored handler may `replaces=` one in code (`module.Class`). An ApplicationService calls the code's
  Features through `self.feature_bus`.
- Retiring is saving inactive; the next generation is built without it.
- Each replica swaps on its own `reload()`; a save by one replica reaches the others only when they
  reload (a cron calling `registry.reload()` is the usual shape).

## Where use cases are kept

`UseCaseStore` is the contract: `active()` and `save(use_case)`. `InMemoryUseCases` needs nothing;
`SqlUseCases(database, table)` (`sincpro_framework.data_layer.orm`, `[sqlalchemy]`) keeps them
in the context's database — replicas share them, and a save by one is loaded by every one on its next
`reload`. The table is declared on the context's own `MetaData` (`use_case_table(metadata)`), so its
migrations create and change it.

## Security

Stored source runs with the service's permissions: whoever can save a use case can run code in it.
Keep saving behind the same review a deploy has — `check` catches what does not load, not what should
not run.

## Reference

| | |
|---|---|
| `RuntimeUseCase(name, source, version=1, active=True, replaces=None)` | a use case as data |
| `BusRegistry(bus, store)` | `current`, `generation`, `reload()`, `check(use_case)`, `put(use_case)`, `check_all()`, `execute(dto_name, payload)`, `in_force` |
| `UseCaseStore` / `InMemoryUseCases` / `SqlUseCases` / `use_case_table` | where they are kept |
| `UseCaseRefused` | a use case that cannot be loaded, named with its version |
| `UseFramework.fresh()` / `.handler_of(dto)` | a new bus of the context / the handler registered now |
