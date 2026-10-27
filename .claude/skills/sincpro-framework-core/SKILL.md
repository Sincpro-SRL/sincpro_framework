---
name: sincpro-framework-core
description: Work the cross-cutting parts of a sincpro_framework bus — opening a context with bus.context (the component itself is sincpro-framework-context), interceptors (veto, adjust, audit, cache, retry; replaces=), error handlers (global/feature/app_service) and dry runs (DryRunnable + preview). Use whenever a task passes metadata through a call, wraps a use case from outside, replaces a use case, maps exceptions to answers, or must show what a write would do before it runs (a preview, a confirmation, dry_run).
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
| `bus.thread_context()` | The bus bound to the caller's context (`in_context`), for one `ThreadPoolExecutor` task — hand its `.execute` to the pool (prefer `ContextExecutor`) | function | method of every `Bus` |
| `AsyncBus` | Stateless `async` facade over a bus (`asyncio.to_thread` per call) | adapter | `from sincpro_framework.aio import AsyncBus` |
| `@bus.interceptor(*Commands)` | Registers a function `(dto, call_next) -> response` around those Commands (all when none) | decorator | method of `UseFramework` |
| `CallNext[R]` | Type of `call_next`: runs the next interceptor or the handler | function | `from sincpro_framework import CallNext` |
| `replaces=` on `@bus.feature` / `@bus.app_service` | Another handler answers **instead** of the registered one | decorator | — |
| `replaces=` on `@bus.interceptor` / `add_*_error_handler` | Another interceptor/handler takes **its place in the chain** | decorator | — |
| `ErrorHandler` | `(error) -> answer`, or re-raise to delegate | function | `from sincpro_framework.error_handler import ErrorHandler` |
| `add_global_error_handler` / `add_feature_error_handler` / `add_app_service_error_handler` | Append a handler to one of three chains | registry | methods of `UseFramework` |
| `ignore_sentry_exceptions(*types)` | Marks error types as answers, not faults: not sent to Sentry, an `info` line, `sincpro.fault="false"` | setting | method of `UseFramework` |
| `ProgrammingError` | Raised when an interceptor changes the Command's or response's class | DTO | `from sincpro_framework import exceptions` → `exceptions.ProgrammingError` |
| `extend(module.attach)` | Install a module: a function (or an object's `attach`) that registers on the bus; before or after the build — after, it answers from the next execution | method | method of `UseFramework`; guide `docs/core/extending.md` |
| `add_dependency(name, dep, replaces=old)` | Put another adapter in the place of a registered one; without `replaces`, a taken name raises `DependencyAlreadyRegistered` | method | method of `UseFramework` |
| `describe` / `features` | What answers a Command and which interceptors wrap it | function | `from sincpro_framework.introspection import describe, features` |
| `CircuitBreaker(name, failures=5, open_for=30s)` | Stops calling a dependency that fails repeatedly: `with circuit:`, `@circuit` (functions, methods, coroutines, generators — guarded while iterated), `circuit.call(fn, ...)`, or handed to an adapter's constructor; open → `CircuitOpen` (`UNAVAILABLE` + `retry_after`, ≥ 1 s) without calling; `probe_for=` (default `open_for`) longer than the slowest call; state where the context stores its leases | component | `from sincpro_framework.circuit_breaker import CircuitBreaker, CircuitOpen`; guide `docs/core/circuit-breaker.md` |

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
  signal. Put request data in locals; `use_context().set(...)` when only what you run should see it.

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
  `UseFramework(name, hide_in_logs=["TOKEN"])` excludes a key from them — or make it a `Secret`.
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
from sincpro_framework.ddd import exceptions
from sincpro_framework import CallNext


@billing.interceptor(CommandCreateInvoice)
def credit_check(dto, call_next: CallNext[ResponseCreateInvoice]) -> ResponseCreateInvoice:
    if dto.customer_id in blocked:
        raise exceptions.DomainError(f"{dto.customer_id} has no credit")
    return call_next(dto)
```

- Adjust the input with `dto.model_copy(update=...)`; what reaches `call_next` must be the same
  Command class, and what comes back the class the handler answered, else
  `ProgrammingError`.
- Registered after the bus was built, an interceptor wraps from the next execution (a new
  generation of the bus); naming a Command nobody answers raises `UnknownDTOToExecute` when that
  generation is built.
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
- A `ClientError` or a `DomainError` is an answer, not a fault: no Sentry, an `info` line,
  `sincpro.fault="false"`. `bus.ignore_sentry_exceptions(SomeError)` (subclasses included) makes
  other errors answers too; `bus.report_sentry_exceptions(SomeError)` makes one a fault. The kind
  (`sincpro.outcome`) never changes. Handlers still run.
- Code that calls the bus (a transport, a wrapper) is outside every chain.

Depth: [references/errors.md](references/errors.md).

**Hearing what a call did.** Three layers hang on the path, none of which changes what the call
answers — that is an interceptor's or an error handler's. Each is heard the same three ways: a
function on one bus, a subscription for the whole process, a `Publisher` for a queue. Nobody
listening costs nothing; a listener that raises is logged and ignored; one may be added at any time
and a `fresh()` generation hears it too.

- **Income** (`sincpro_framework.bus_pipeline.incomes`): every Feature and ApplicationService that
  begins emits an `ExecutionStarted` — the DTO as it was handed, the flow's context — after the
  context providers and **before the interceptors**. `@bus.on_start`, `start_listeners.subscribe(fn)`,
  `bus.publish_starts(to=…)`. Depth: `docs/core/incomes.md`.
- **Outcome** (`sincpro_framework.bus_pipeline.outcomes`): every one that answered emits an
  `ExecutionCompleted` — the DTO, the response; none when an error handler answered for it. When no
  handler answered and the exception reaches the caller, an `ExecutionFailed` goes with it — once,
  where it left the call, naming the use case where it first failed, with its `kind` and
  `retry_after`; the exception carries `failure_id`. `@bus.on_completion` / `@bus.on_failure`,
  `completion_listeners.subscribe(fn)` / `failure_listeners.subscribe(fn)`, `bus.publish_completions(to=…)` /
  `bus.publish_failures(to=…)`. Every one of them hears less when told: `use_cases=[Command, ...]`,
  `when=Condition(field="context.tenant_id", value="acme")` (any criteria filter over the event by
  path — `kind`, `dto.amount`, `response.total`; a list must all hold; also as JSON), and the
  decorator takes them (`@bus.on_failure(use_cases=[...])`). Depth: `docs/core/outcomes.md`.

**Saying what a write would do** (`from sincpro_framework import DryRunnable, Preview`). A Command that inherits
`DryRunnable` carries `dry_run: bool = False`; its handler adds `def preview(self, dto) -> Preview`
(`text`, `data`) that validates and reads, never writes. `bus(CommandX(..., dry_run=True))` runs as
any call — context, guards, interceptors, span — and is answered by `preview`; `execute` never sees
a dry run (a handler with no `preview` gets it in `execute`, and decides). Every caller says it in
the Command — a CLI flag, a REST body, an MCP argument. An ApplicationService previews by running
its Features dry (`self.feature_bus(cmd.model_copy(update={"dry_run": True}), Preview)`). Writes
previewed by default: `dry_run: bool = True` on the project's own base. Never a hand-made
`confirmed` flag raising an exception. Depth: `docs/core/dry-run.md`.

**Running a job of steps** (`sincpro_framework.pipelines`). A `Pipeline` is an ApplicationService that is
the order of its steps, declared as data — `class RunNightly(Pipeline, ApplicationService): steps =
[CommandCollect, CommandBill]; always = CommandClose` — registered with `@bus.app_service(Command)`.
Each step is a Feature of the same bus, filled from the input by the names of its fields; `ok=False`
or an exception ends the run, `always` runs last with `ok` and `failure`. The bus attaches an
interceptor (`PipelineRecorder`) that stores the run — a `PipelineRun` — in the context
(`PipelineRuns.of()`), beside the drafts; the same `run_id` again starts over, or goes on
with `resume = Resume.CONTINUE`. `by = "<field>"` picks one of several recipes; `records = False`
leaves the recorder out. Nothing is refused: no tenant, no store or a step the bus does not answer
is said in the logs. When it needs more, one line each: `retry = Retry()` runs again a step whose
failure is worth it by its kind (unavailable, conflict, in progress — never `ok=False` nor a domain
refusal); `Await("approval")` in the steps makes the run wait (`PipelineState.WAITING`) until the same
`run_id` is run with the input's `approval` filled; `GetRun[Command]` / `FindRuns[Command]` answered
by a `PipelineReads` Feature read the runs on any wire; `lease = timedelta(minutes=2)` makes it one run
of a `run_id` at a time across replicas, renewed while it runs, and `PipelineRuns.of().abandoned(…)`
lists the runs whose worker died. Depth: `docs/core/pipelines.md`, `docs/core/leases.md`.

**Guarding a call out of the process** (`sincpro_framework.circuit_breaker`). An adapter that reaches
SIAT, a gateway or another API takes a `CircuitBreaker("siat")` in its constructor (registered once in
`dependencies.py`, shared by every Feature) and wraps its calls `with self.circuit:` — or decorates
the method, or `circuit.call(...)`. After `failures` calls in a row that say the dependency is not
answering (`UNAVAILABLE`, `UNKNOWN_OUTCOME`, `EXHAUSTED`, or an undeclared crash — never an answer
like `DomainError` or not found; `counts=` overrides) it opens: calls raise `CircuitOpen` at once,
then one call is let through to try. A cancelled or interrupted call (`CancelledError`,
`KeyboardInterrupt`, a generator closed early) neither counts nor clears — put the deadline inside
the guarded call, not `asyncio.wait_for` around it. "In a row" is approximate under concurrency.
No store is named: it reads where the running context stores
its leases (memory, or Redis shared by replicas); a store that does not answer lets calls through.
A Feature never holds a circuit, and retrying is the orchestrator's (pipeline `Retry`, a queue) —
one layer only. A bus hosted elsewhere has one only when asked: `?circuit_breaker=on` on its address, or
`hosted_by(address, circuit_breaker=True | CircuitBreaker(...) | False)` in code, which wins; it counts only what
the caller's transport raised (`error.unreached`), never what the host answered — not even the host's
own `CircuitOpen`. Depth: `docs/core/circuit-breaker.md`.

An income and its outcome name the same `execution_id`. What a listener must not do: hold state a
process depends on (it may not run) or change the answer. The whole path, step by step:
`docs/core/execution-path.md`.


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
- Real idempotency and query caching — `bus.cache`, `bus.cache.queries(Query)`, `bus.idempotency` on every bus, stored where its context is stored: `sincpro-framework-caching`

**Interceptors as classes, and for a base.** `@bus.interceptor(CommandX) class CheckCredit(Interceptor):
def intercept(self, dto, call_next)` — dependencies injected as a Feature's (`self.<name>`, typed by
the context's own base `class Interceptor(_Interceptor, DependencyContextType)`), one made per
generation. `@bus.interceptor(WriteCommand, subclasses=True)` wraps every subclass of a base no
handler answers. A dependency registered as `None` is replaced with `add_dependency(name, dep,
replaces=None)`.
