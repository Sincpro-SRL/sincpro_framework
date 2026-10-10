---
name: sincpro-framework-core
description: Work the cross-cutting parts of a sincpro_framework bus — opening a context with bus.context (the component itself is sincpro-framework-context), interceptors (veto, adjust, audit, cache, retry; replaces=) and error handlers (global/feature/app_service). Use whenever a task passes metadata through a call, wraps a use case from outside, replaces a use case, or maps exceptions to answers.
---

# sincpro-framework-core

The parts of the bus that are not a Feature: metadata that travels with a call, code that runs
around a use case, and how a failure becomes an answer. The Feature/ApplicationService recipe itself
is in the `sincpro-framework` skill.

## Context

Inside a service, one `UseFramework` bus per bounded context answers Commands with Features and
ApplicationServices. Three concerns cut across every use case of that bus: **who/what the call is
about** (correlation id, user, tenant), **rules applied from outside a handler** (veto, audit,
cache, retry, an addon answering instead), and **turning exceptions into answers**. This skill
covers those three: `bus.context(...)`, `@bus.interceptor(...)` / `replaces=`, and the
`add_*_error_handler` chains.

- It is **not** a place for business rules about an aggregate (a repository hook,
  `sincpro-framework-persistence`), for reacting after the fact in another context (a domain event,
  `sincpro-framework-domain-events`), or for permissions (`sincpro-framework-auth`).
- It is **not** an HTTP middleware layer: interceptors and handlers see Commands, never requests.
  The transport adapter (`sincpro-framework-entrypoints`) opens the context and calls the bus.
- Don't use an interceptor when the rule belongs to one use case only: write it in that handler.

## Abstractions

| Term | What it is | Kind | Import |
|---|---|---|---|
| `UseFramework` | The bus of one bounded context; registers handlers, dependencies, interceptors, error handlers | registry | `from sincpro_framework import UseFramework` |
| `bus.context(mapping)` | Context manager: opens metadata for every call made inside the `with` | function | method of `UseFramework` (returns `FrameworkContext`) |
| `self.context` | What a Feature/ApplicationService reads: the context of the execution in progress | DTO | attribute of `Feature` / `ApplicationService` |
| `bus.current_context()` | The context in play as this bus sees it, read-only — the object `self.context` is | function | method of `UseFramework` |
| `use_context()` | The same context from anywhere, no bus in hand (`sincpro-framework-context`) | function | `from sincpro_framework.context import use_context` |
| `ThreadContextBus` | A bus handle bound to a captured context, for one `ThreadPoolExecutor` task (prefer `ContextExecutor`) | adapter | `from sincpro_framework.sincpro_abstractions import ThreadContextBus` |
| `AsyncBus` | Stateless `async` facade over a bus (`asyncio.to_thread` per call) | adapter | `from sincpro_framework.aio import AsyncBus` |
| `@bus.interceptor(*Commands)` | Registers a function `(dto, call_next) -> response` around those Commands (all when none) | decorator | method of `UseFramework` |
| `CallNext[R]` | Type of `call_next`: runs the next interceptor or the handler | function | `from sincpro_framework import CallNext` |
| `replaces=` on `@bus.feature` / `@bus.app_service` | Another handler answers **instead** of the registered one | decorator | — |
| `replaces=` on `@bus.interceptor` / `add_*_error_handler` | Another interceptor/handler takes **its place in the chain** | decorator | — |
| `ErrorHandler` | `(error) -> answer`, or re-raise to delegate | function | `from sincpro_framework.error_handler import ErrorHandler` |
| `add_global_error_handler` / `add_feature_error_handler` / `add_app_service_error_handler` | Append a handler to one of three chains | registry | methods of `UseFramework` |
| `ignore_sentry_exceptions(*types)` | Marks error types as expected traffic: not sent to Sentry, outcome `expected` | setting | method of `UseFramework` |
| `ProgrammingError` | Raised when an interceptor changes the Command's or response's class | DTO | `from sincpro_framework.exceptions import ProgrammingError` |
| `BusAlreadyBuilt` | Raised when registering an interceptor/handler/dependency after the bus built | DTO | `from sincpro_framework.exceptions import BusAlreadyBuilt` |
| `describe` / `features` | What answers a Command and which interceptors wrap it | function | `from sincpro_framework.introspection import describe, features` |

Look-alikes: `bus.context(...)` **opens** a context; `self.context` **reads** it inside a handler;
`bus.current_context()` **reads** it from anywhere else. `replaces=` on a Feature swaps the handler;
`replaces=` on an interceptor swaps an interceptor; neither changes the Command.

## Architecture

**In the framework** (`sincpro_framework/`, primary dependencies only — nothing here needs an extra):

```text
use_bus.py         UseFramework: registration, build (lazy, once, under a lock), context, error chains
bus.py             FrameworkBus → FeatureBus / ApplicationServiceBus: dispatch by DTO class,
                   run_around(interceptors), then the scope's error handler
interceptors.py    CallNext, the chain and its class contract
error_handler.py   ErrorHandler and the chain (first registered runs first)
ordering.py        one order for every extension point: before/after, sequence, registration
context/           the context component — a tree of nodes over a ContextVar
                   (`sincpro-framework-context`)
aio/               AsyncBus
```

The context is its own component (`sincpro-framework-context`); this skill only opens it. There are
no ports or optional adapters in this skill's scope; tracing/Sentry around the same calls are the
observability extras.

**In a consumer service:**

```text
domains/<ctx>/
├── __init__.py              <ctx> = config_<ctx>_framework("<ctx>"), then `from . import services`,
│                            then `from . import interceptors`
├── interceptors.py          @<ctx>.interceptor(...) functions (imported by __init__.py)
├── infrastructure/
│   ├── framework.py         typed Feature/ApplicationService bases + config_<ctx>_framework()
│   ├── dependencies.py      register_dependencies(bus): add_dependency(...), add_*_error_handler(...)
│   └── error_handler.py     the error handler functions
├── services/                one Feature/ApplicationService per file
└── adapters/                read bus.current_context() when they need the caller
entrypoints/                 open bus.context({...}) per request, then call the bus
```

**One call:**

```text
entrypoint: with bus.context({"correlation_id": ..., "user_id": ...}):
  bus(Command)
   └─ FrameworkBus ── global error chain ───────────────────────────────┐
       └─ ApplicationServiceBus / FeatureBus ── its scope's error chain ─┤ re-raise → next
           └─ interceptor 1 → interceptor 2 → handler.execute(dto)       │
                 (self.context = the open context; nested buses inherit it)
  ← response, or what the first handler that does not re-raise returned ┘
```

## Mistakes an agent makes

- **An error handler that only logs.** It returns `None`, that `None` becomes the bus's answer and
  the failure disappears. A watching handler ends with `raise error`.
- **Trusting `bus(dto, Response)` as a type check.** `return_type` is a typing hint only; when a
  handler answers with a dict or `None`, the caller receives that. Validate where it matters.
- **A feature error handler that answers inside an ApplicationService.** `self.feature_bus(...)`
  then returns the handler's value and the orchestration continues on it. Register feature
  handlers that answer only when every caller can take that answer; otherwise re-raise.
- **`executor.submit(bus, dto)` from a `with bus.context(...)` block.** The worker sees an empty
  context, no error. Use `ContextExecutor` / `in_context(bus)` (`sincpro-framework-context`), or
  `bus.get_async_bus()` from `async def`.
- **An interceptor module that is never imported.** The decorator never runs and nothing wraps the
  Command, silently. Import it from the context's `__init__.py` before the first call.
- **`@bus.interceptor()` for an audit.** It wraps every Command, including the Features an
  ApplicationService runs through `self.feature_bus`, so one request writes several audit rows.
  Name the Commands.
- **An interceptor that answers without `call_next`.** The handler never runs and nothing checks
  the answer's class. Only do it for a deliberate cache or veto.
- **`bus.context(..., global_scope=True)` per request.** It publishes the keys to every concurrent
  execution of that bus on every thread while the block is open. Use the default (isolated) form.
- **Writing `self.context[...]` as scratch space.** A mapping write lands on the scope of the call:
  it stays for the rest of the enclosing `with bus.context(...)` block and goes on every later
  signal. Keep request data in locals; `use_context().set(...)` when only what you run should see it.

## Opening a context

```python
with app.context({"correlation_id": "123", "user_id": "admin"}) as app_with_context:
    result = app_with_context(dto)          # app_with_context is app itself

with app.context({"env": "prod", "user_id": "admin"}) as outer:
    with outer.context({"env": "staging"}) as inner:
        inner(dto)          # env="staging", user_id="admin"
```

The context is its own component — a tree from the process down to each execution, read from
anywhere with `use_context()`, two ways to write, threads, providers, required keys, stores and
propagation: **`sincpro-framework-context`**. What matters here:

- Every context key goes on log lines, spans and error events of that bus;
  `UseFramework(name, hide_in_logs=["TOKEN"])` keeps a key off them — or make it a `Secret`.
- A bus called from inside another's execution — or inside its `with` — runs under the caller's
  context; nothing it adds flows back.
- A typed `self.context`: `Feature[Command, Response, MyContext]` with `MyContext` a `TypedDict`, and
  `bus.context_schema(MyContext)` to validate it at the door.

Depth: [references/context.md](references/context.md) and the `sincpro-framework-context` skill.

## Interceptors: change a use case from outside it

A function runs **around** a Command: it sees the Command and the response, can veto, adjust either,
or answer by itself, and never changes either's class. They run outermost first, **however the
Command is executed** (`bus(dto)`, `self.feature_bus(dto)`, a workflow step, a cron tick).

```python
from sincpro_framework.ddd import DomainError
from sincpro_framework import CallNext


@billing.interceptor(CommandCreateInvoice)
def credit_check(dto, call_next: CallNext[ResponseCreateInvoice]) -> ResponseCreateInvoice:
    if dto.customer_id in blocked:
        raise DomainError(f"{dto.customer_id} has no credit")
    return call_next(dto)
```

- Adjust the input with `dto.model_copy(update=...)`; what reaches `call_next` must be the same
  Command class, and what comes back the class the handler answered, else
  `ProgrammingError`.
- Fixed when the bus is built: registering after raises `BusAlreadyBuilt`; naming a Command nobody
  answers raises `UnknownDTOToExecute` at build.
- Ordering: `before=[fn]`, `after=[fn]`, `sequence=` (lower first, 10 default), then registration
  order. `bus.without_interceptor(fn)` switches one off.

**`replaces=`** registers a handler that answers **instead** of the registered one; the replaced
handler does not run, interceptors still wrap whichever answers:

```python
@tax.feature(CommandComputeTax, replaces=ComputeTax)
class ComputeTaxBolivia(Feature): ...
```

`replaces=` must name the handler registered at that point; the response contract stays (Liskov). It shows in
the build log, the span (`sincpro.replaces`) and `introspection.describe(tax, CommandComputeTax)`.

Depth: [references/interceptors.md](references/interceptors.md).

## Error handlers: a failure becomes an answer

Three chains: **feature** (errors raised in a Feature), **app service** (in an ApplicationService),
**global** (anything that leaves those two). First registered runs first; re-raise to delegate to the
next, and from the last one to the next scope out.

```python
framework.add_global_error_handler(handler)
framework.add_feature_error_handler(handler)
framework.add_app_service_error_handler(handler)
```

- Handlers can be added before or after the first execution; same `replaces=`, `before=`, `after=`,
  `sequence=` as interceptors, and `without_error_handler(fn)`.
- **What a handler returns becomes the bus's answer.** Watching handlers re-raise.
- `bus.ignore_sentry_exceptions(SomeError)` (subclasses included) marks expected traffic: never sent
  to Sentry, metric outcome `expected`. Handlers still run.
- Code that calls the bus (a transport, a wrapper) is outside every chain.

Depth: [references/errors.md](references/errors.md).

**Hearing what a call did** (`sincpro_framework.bus_pipeline.outcomes`). Every Feature and ApplicationService
that answered emits an `ExecutionCompleted` — the DTO, the response, the execution, the flow's
context; none when an error handler answered for it. When no handler answered and the exception
reaches the caller, an `ExecutionFailed` goes with it — once, where it left the call, with its
`kind` and `retry_after`; the exception carries `failure_id`. Listen with `@bus.on_completion` /
`@bus.on_failure` (one bus), `completions.subscribe(fn)` / `failures.subscribe(fn)` (the process),
or `bus.publish_completions(to=…)` / `bus.publish_failures(to=…)` (a queue). Nobody listening costs
nothing. Depth: `docs/core/outcomes.md`.


## References

- [references/context.md](references/context.md): propagation, threads, async, global scope
- [references/interceptors.md](references/interceptors.md): contract, recipes, ordering, `replaces=`
- [references/errors.md](references/errors.md): scopes, chaining, the swallow trap

The framework repository holds the long-form guides (`docs/core/context-manager.md`,
`docs/core/interceptors.md`, the README "Error Handling" section, PRD_04); this skill does not depend
on them.

## Related

- Bootstrap, hard rules, DTO/dependency typing: `sincpro-framework`
- Metrics, traces, errors reporting, `with_trace`: `sincpro-framework-observability`
- Auth is the outermost interceptor: `sincpro-framework-auth`
- Real idempotency and query caching: `sincpro-framework-caching`
