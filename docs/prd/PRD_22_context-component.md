# PRD_22: The context as a component — read from anywhere, scoped, typed, carried, and kept when asked

- **Status**: built 2026-10-02, every phase, uncommitted — `use_context()` and the tree, threads,
  providers, schema, requirements, secrets, one propagator, stores and a shared root, `to_client()`;
  the guide is `docs/core/context-manager.md`, and it runs. Decisions: one facade
  (`use_context()`, `self.context` and `bus.context(...)` are one object, wherever it is reached);
  a store configured wherever wanted (the process, a bus, a block); required keys raise when a use
  case declared them, and nothing is checked when it did not; no abbreviations in the API
  (`context.level`, `context.parent`).
- **Depends on**: PRD_21 (execution identity, `context/execution.py`, `context/keys.py`), `caching`
  (`KeyValueStore` and its Redis/Valkey/Memcached adapters), `settings` (`SincproConfig`, `Secret`),
  the transports (`remote_execution`, FastStream, REST/JSON-RPC/gRPC entrypoints).
- **The contract that never breaks**: the syntax projects already love —
  `with bus.context(SIATContext(TOKEN=…, SIAT_ENV=…)):`, `bus.context({...})`, nested `with`,
  `self.context` in a handler, `bus.current_context()`, `global_scope=True`.
- **Philosophy**: one component, three levels, the nearest one wins — the way a React `Context`
  is one mechanism and a provider's place in the tree decides its scope. Immutable underneath, so
  threads and replicas never tear it. Everything beyond the in-process default is opt-in.

## 1. Requirements (from the owner)

1. Read the context **from anywhere** (`use_context()`) — a Feature, an interceptor, a hook, an adapter, the
   infrastructure — without holding the bus.
2. **Threads and async work as expected.**
3. **Scopes**: a global (process) context, a flow context, an execution context; nested scopes.
4. **Serializable**: between services of the same language with the types back (enums, datetimes,
   DTOs); to other languages and the frontend as plain JSON.
5. **External context, when wanted**: Redis or a database, shared by microservices — distinct from a
   cache.
6. **Providers**: "a hook where I say *I want context*" — a bus that injects context on execution.
7. **Fast.**
8. The package laid out like the others: `domain`, `adapters`, `infrastructure`, `entrypoint`,
   `__init__`.

## 2. Research

### 2.1 The mechanism: `contextvars` (PEP 567)

| Fact | Source | Consequence |
|---|---|---|
| A `Context` is an immutable mapping (HAMT); `copy_context()` is O(1) | PEP 567, the `contextvars` docs | snapshots are free; immutability is native |
| An asyncio task captures a copy of the context when it is created | PEP 567 | async works with nothing to do |
| `asyncio.to_thread` copies the context; `loop.run_in_executor` and `ThreadPoolExecutor.submit` do **not** — the documented fix is `executor.submit(copy_context().run, fn)` | bpo-34014, PEP 567 | a pool loses the context unless the framework hands it on |
| A plain `threading.Thread` starts with an empty context on the GIL build; Python 3.14 adds `sys.flags.thread_inherit_context`, **true on the free-threaded build, false on the default one** | What's new in 3.14 | behaviour differs by build: the framework hands it on explicitly so it is the same everywhere (3.12 included) |
| `Token` objects are context managers in 3.14 | What's new in 3.14 | `with var.set(x):` where available |

Measured on this machine (Python 3.12, `poetry run`):

| Operation | Cost |
|---|---|
| `ContextVar.get()` | 11 ns |
| get + one key | 21 ns |
| open and close a scope (`set` + `reset`) | 47 ns |
| `copy_context()` (hand-off to a thread) | 18 ns |
| a child scope merging 20 keys | 0.7 µs |
| typed dump of a 3-key context (pydantic) | 0.5 µs |
| typed load, the `IntEnum` back as itself | 0.3 µs |

Reading the context from anywhere costs nanoseconds; the only microsecond costs are at a scope's
opening and at a boundary — never per read.

### 2.2 How others carry it

| System | What we take |
|---|---|
| **Go `context.Context`** | immutable; a child per value; deadline and cancellation travel |
| **OpenTelemetry Context + Baggage** | one propagator API (`inject`/`extract`) for every transport; baggage is the part that travels, for "information available at the start of a request further downstream"; caveat — baggage reaches third parties and carries no integrity |
| **W3C Baggage** | header `baggage`, text values only, ≤ 8192 bytes, ≤ 180 members — interoperable, but a list (`tenant_ids`) or a typed value needs encoding |
| **Odoo** | `env.context` immutable, `with_context` returns a new one; the web client's `user_context` (`allowed_company_ids`, `lang`, `tz`) is added to every RPC by the ORM service and returned in `session_info` — the context crosses to the frontend and back |
| **structlog `bind_contextvars`, starlette-context, asgi-correlation-id** | request-scoped context over `contextvars`, bound at the entrance; each solves one transport and logs, none layers scopes, types, providers or a store |

**No library does what is asked as a whole**; all good ones stand on `contextvars`. The framework
already ships pydantic (typed serialization, fast) and `KeyValueStore` (Redis, Valkey, Memcached):
**no new dependency is needed**.

### 2.3 What the consumers show

`sincpro_siat_soap` and the Odoo `facturacion_electronica*__bo` addons (read-only review): the
`with siat_soap_sdk.context(SIATContext(...))` syntax at ~22 call sites, a typed `TypedDict` schema,
three simple keys — and the costs of a context that is not yet a component: the same block rebuilt at
every site, no runtime validation of the schema, secrets without `hide_in_logs`, a manual
`thread_context()` per pooled task, a plain thread losing the context, background work rebuilding it
record by record. Each piece below answers one of them.

### 2.4 What the framework has today

Six places hold context state (`_overlay_var` and `_in_global_var` per bus, `_shared_context` +
`_live_overlays`, `_executing`, `_live`, observability's `_said`, PRD_21's `_running`), and it travels
in six shapes (`x-sp-request-context` pickled, `sincpro-context` JSON, `sp-ctx-<key>`, the identity
headers, a JSON-RPC body `context`, CloudEvents `ce_*`). The per-call dict is mutable and shared by
every execution of a call; `global_scope=True` writes into executions already running. The audit
found two defects of this origin (async isolation, an overlay removed by equality).

## 3. The model: a tree of scopes, never one context overwritten

```
process                        { lang: "es", tz: "America/La_Paz" }        the root, one per process
 └─ flow                        { user_id: "ana", tenant_id: "acme" }        an entrance, bus.context(...)
     └─ execution ConfirmSale   { execution_id: E1 }                         every use case, on its own
         ├─ execution CheckCredit   { execution_id: E2 }
         └─ scope                   { tenant_id: "beta" }                    use_context().scoped(...)
             └─ execution IssueInvoice { execution_id: E3 }                  reads tenant_id = "beta"
```

1. **A node keeps only its own keys and a pointer to its parent.** Nothing is overwritten: reading walks
   up the chain and the nearest node wins — a React provider's rule.
2. **Closing a node is returning to its parent.** The scope's `tenant_id="beta"` is gone when its block
   ends; `ConfirmSale` still reads `acme`.
3. **`contextvars` answers one question — which node is current, per thread and per async task.** The
   tree (parents, inheritance, where each value came from) is the component's own, on top of it.
4. **A node's keys are an immutable mapping, replaced whole on a write** (copy-on-write, one reference
   swapped): a reader in another thread sees the old mapping or the new one, never a torn one.

### 3.1 The levels

| Level | Opened by | Holds | Travels | Today |
|---|---|---|---|---|
| **process** | the start of the process (`use_context().process`) | settings (read through), defaults (`lang`, `tz`, a default tenant), flags | no — shared by replicas only through a store | half: `global_scope=True`, which writes into running executions |
| **flow** | an entrance (REST, RPC, gRPC, a consumer, a cron), `bus.context(...)`, a restored store entry | `user_id`, `tenant_id`, `tenant_ids`, `lang`, `tz`, the correlation, the project's keys (`TOKEN`, `SIAT_ENV`) | yes | yes, as one mutable dict shared by every execution of a call |
| **execution** | every `execute` of a Feature or an ApplicationService, by itself | the identity (PRD_21), and what the handler sets | only as the cause of the next | only the identity |
| **scope** | `with use_context().scoped(...)`, a nested `bus.context(...)` | what the block changes for what it calls | with its flow | as nested `bus.context` |

**Hooks and interceptors run inside the node of the use case that fires them**: they read what that
execution sees, and `.parent` / `.flow` for what is above it.

## 4. The API — the syntax kept, the reach extended

```python
# unchanged
with bus.context(SIATContext(TOKEN="…", SIAT_ENV=SIATEnvironment.TEST)):
    bus(CommandSendDocument(...))
self.context["TOKEN"]                                   # in a handler, as today
bus.current_context()

# new: from anywhere — a handler, an interceptor, a hook, an adapter — no bus in hand
from sincpro_framework.context import use_context

ctx = use_context()
ctx["tenant_id"], ctx.get("lang", "es")                 # the effective value: the nearest node wins
ctx.execution                                           # PRD_21's identity
ctx.settings(SiatSettings)                              # settings read through, never copied
ctx.to_client()                                         # what the frontend may see
```

### 4.0 Changing it, and where a change reaches

```python
use_context().set("tenant_id", "beta")                  # the current node
with use_context().scoped(tenant_id="beta"):            # a child node, gone when the block ends
    billing(CommandIssueInvoice(...))
use_context().flow.set("lang", "en")                    # explicit: the flow, for every later execution of it
use_context().process.set("maintenance", True)          # explicit: the process; replicas see it via a store
```

| A `set` on the current node reaches | Because |
|---|---|
| ✅ everything it runs **afterwards** — child executions, hooks, other buses, threads started with `ContextExecutor` | they read through their parent |
| ❌ its **parent** | an execution never changes whoever called it |
| ❌ a **sibling** execution | each has a node of its own |

**`self.context[key] = value` keeps working and becomes `use_context().set(key, value)`** — with one
change of behaviour, to approve with this PRD: today a write in a Feature is seen by the *next
sibling* Feature, because both share one dict; from now on it reaches only what that Feature runs.
Reaching the siblings is `flow.set(...)`, said on purpose.

### 4.0.1 The parent, and where a value came from

```python
ctx.parent["tenant_id"]                                 # what the level above says
ctx.own                                                 # only this node's keys
ctx.level                                               # Level.EXECUTION
ctx.origin("tenant_id")                                 # Origin(level, node, execution_id) that set it
ctx.lineage()                                           # every node up to the process, for debugging
```

For an audit ("this Feature ran under tenant `beta` while the request came for `acme` — who changed
it?"), for a hook that needs the flow's value rather than an intermediate override, for a span that
says which level gave each attribute.

### 4.1 Threads and pools — the same on every Python build

```python
ContextThread(target=work).start()                      # a Thread that starts in the caller's context
executor = ContextExecutor(max_workers=8)               # a ThreadPoolExecutor that hands it on per task
executor.submit(in_context(fn), *args)                  # one function, for a pool the project owns
```

`thread_context()` stays and is built on these. `asyncio` needs nothing.

### 4.2 Types and validation

The schema a project already declares — a `TypedDict` (`SIATContext`) or a DTO — is the context's
type: what enters from outside (a header, a store, the frontend) is validated against it once, at
the boundary; reads are typed for the editor. Strings stay accepted.

### 4.3 Providers — "I want context"

```python
@siat_soap_sdk.context_provider(needs=["nit_id"], gives=["TOKEN", "SIAT_ENV"])
def siat_credentials(context: Mapping[str, Any]) -> Mapping[str, Any]:
    ...

with siat_soap_sdk.context(nit_id=nit.id):              # TOKEN and SIAT_ENV provided on execution
```

A function the bus runs when an execution opens and the context has what it `needs` and lacks what it
`gives`; ordered like every extension point (`before`/`after`, `replaces`); computed once per scope.

### 4.4 Required keys and secrets

```python
@bus.feature(CommandSendDocument)
@requires_context("TOKEN", "SIAT_ENV")
class SendDocument(Feature): ...
```

Declared and missing → `ContextRequired` before the handler runs, through the bus's error handling:
a call under the wrong tenant is the dangerous case. Nothing declared, nothing checked — what almost
every use case is. A value of type `Secret` (the
settings' own) is masked on every signal and never written by a codec meant for another process
unless that codec is told to.

### 4.5 Serialization — two codecs

| Codec | For | Types |
|---|---|---|
| **Typed** (pydantic over the declared schema) | the same codebase: `remote_execution`, a store, a worker | come back as themselves: enums, datetimes, DTOs, `Secret` |
| **Plain JSON** | brokers, the frontend, other languages | text, numbers, booleans, lists |

The W3C `baggage` header is **read and written as well**, for interoperability with OpenTelemetry
and other stacks; the framework's own header stays JSON, because baggage carries text only.

### 4.6 A context kept outside — opt-in

```python
use_context().root.set(ContextStore, KeyValueContexts(RedisKeyValue(redis)))   # the process
sales.context_store(KeyValueContexts(RedisKeyValue(redis)))                     # or one bus

with bus.context(SIATContext(...), keep_as="sale-77", ttl=timedelta(hours=1)):
    bus(CommandStartSale())
with bus.context(restore="sale-77"):                    # a worker, a cron, another microservice
    bus(CommandContinueSale())
```

- **A flow resumed elsewhere**, by key or by `correlation_id`.
- **A session**: the frontend sends an id, the entrance restores its context — what Odoo's session is.
- **The process level shared by replicas**: flags and defaults, re-read only when their version
  changed (the way `Workflows.refresh()` works) — never a network call per read.

The port: `keep(key, context, ttl)`, `restore(key)`, `forget(key)`, `version()`. Adapters:
in memory (core), over `KeyValueStore` (core — its Redis/Valkey/Memcached adapters are already
extras), over the Repository (`orm/`, optional). **Not a cache**: a cache keeps what can be
recomputed; this keeps what a flow *is*, written by the flow, read by whoever continues it.

### 4.7 One propagator for every transport

`inject(context) -> headers` and `extract(headers) -> context`, used by `remote_execution`,
FastStream, REST, JSON-RPC, gRPC. The six current shapes are read during a transition; the written
shape is one: `sincpro-context` (JSON) + the identity headers + `baggage`.

## 5. The package

```
sincpro_framework/context/
  __init__.py      the public API: use_context, Level, Origin, ContextThread,
                   ContextExecutor, in_context, Execution, the standard keys, the ports
  domain/          the vocabulary and the ports, no I/O
                   node.py       ContextNode — own keys (immutable, copy-on-write), parent, level
                   level.py      Level (StrEnum): PROCESS · FLOW · EXECUTION · SCOPE; Origin
                   execution.py  Execution, chained (PRD_21)
                   keys.py       the standard keys, legacy keys
                   store.py      ContextStore — the port (memory, key-value, repository)
                   provider.py   ContextProvider registration
  infrastructure/  the ContextVar machinery: the levels in play, opening and closing scopes,
                   handing on to threads and pools; the process level's copy-on-write cell
  adapters/        InMemoryContexts, KeyValueContexts; codecs: typed, plain JSON, baggage
  entrypoint/      what a project and a bus hold: bus.context(...) (FrameworkContext), the mixin,
                   the propagator (inject/extract), thread_context
```

The current modules move into it; every import path of today keeps working (re-exports), the way
`ddd.entity` re-exports `uuid7`.

## 6. Phases

| Phase | What | Breaks nothing because |
|---|---|---|
| **1 — the core** | `ContextSnapshot` and the three levels on `contextvars`; `use_context()` from anywhere; `bus.context` and `self.context` on it; the package layout; `global_scope` no longer writes into running executions | the syntax and the reads are the same; `self.context[...] = …` keeps working, writing a child scope, with a warning |
| **2 — threads** | `ContextThread`, `ContextExecutor`, `in_context`; `thread_context()` kept | additive |
| **3 — types, providers, required, secrets** | schema validation at the boundary, `context_provider`, `requires_context`, `Secret` | each opt-in |
| **4 — one propagator** | `inject`/`extract`, the two codecs, `baggage`; every transport on it | the old shapes are read |
| **5 — kept outside** | `ContextStore`, memory + key-value adapters, `keep_as`/`restore`, the process level across replicas | opt-in |
| **6 — the frontend** | `to_client()`, an entrance that restores a session | additive |

Each phase is approved on its own; phases 1 and 2 first — they remove the races and the lost
context, the defects that exist today.

## 7. Performance budget

A read stays at ContextVar cost (≈ 20 ns). A scope's opening stays under a few microseconds
(a merge of its keys). Serialization happens only at a boundary (≈ 0.5 µs for a small context). A
store is read once per scope, never per key; the process level is re-read only when its version
moves. Each phase adds a benchmark to `tests/` that fails above its budget.

## 8. Decisions open

1. **Writes to `self.context` mid-execution** — proposed: they write the current execution's node,
   reaching what it runs and never its siblings (§4.0); `flow.set` reaches the siblings on purpose.
2. ~~`context_store`~~ — decided: wherever wanted. A store is a value kept under its type
   (`ContextStore`): in the process (`root.set`), on a bus (`bus.context_store`), or given to one
   block (`store=`); the nearest wins.
3. ~~The process level shared across replicas~~ — built: `use_context().root.share(store, every=…)`.
4. ~~Required keys~~ — decided: refused when declared, never checked when not.
5. ~~`bus.current_context()`~~ — decided: the same object as `use_context()`, read-only (its
   documented contract).

## 9. As built

- **Levels**: `ROOT`, `BUS` (what a bounded context publishes — what `global_scope=True` writes),
  `ENTRYPOINT` (with its `EntrypointKind`), `APPLICATION`, `FEATURE`, `HOOK` (each run of a
  repository's hook), `SCOPE`.
- **`Context` is a `dict`** of what its node sees at the moment it is asked for — `json.dumps`,
  `**context`, `isinstance(..., dict)` keep working; navigation and `set` on top. A mapping write
  lands on the scope of the call (as `self.context` always did); `set` on the node only.
- **A value under a type** (`context[SiatSettings]`) is how settings, clients and stores are found;
  only text keys travel.
- **Entrances open their flow with a kind**: REST, JSON-RPC and gRPC through the scalar executor,
  FastAPI, MCP, a queue consumer, `remote_execution`, a cron (`carrying(..., EntrypointKind.CRON)`).
- **One propagator** (`context/adapters/propagation.py`): `inject` / `extract` — `baggage`,
  `sincpro-context`, the identity headers; brokers, HTTP and gRPC entrances use it; a frontend sends
  `sincpro-context` back.
- **Measured** (Python 3.12, this machine): a read 29 ns, `use_context()` under 1 µs, a
  `bus.context(...)` scope ≈ 4 µs; a budget test pins them.
- **Found while building**: the debug line of `bus.context(...)` printed every value of the context
  (a hidden one included — the audit's first finding); it now names the keys only.

## Sources

- [PEP 567 — Context Variables](https://peps.python.org/pep-0567/)
- [contextvars — Python docs](https://docs.python.org/3/library/contextvars.html)
- [What's new in Python 3.14 — thread_inherit_context](https://docs.python.org/3.14/whatsnew/3.14.html)
- [bpo-34014: run_in_executor should propagate contextvars](https://bugs.python.org/issue34014)
- [OpenTelemetry — Baggage](https://opentelemetry.io/docs/concepts/signals/baggage/)
- [W3C Baggage — HTTP header format](https://github.com/w3c/baggage/blob/main/baggage/HTTP_HEADER_FORMAT.md)
- [Odoo — Framework overview (user context)](https://www.odoo.com/documentation/19.0/developer/reference/frontend/framework_overview.html)
- [structlog — Context Variables](https://www.structlog.org/en/stable/contextvars.html)
- [starlette-context](https://starlette-context.readthedocs.io/en/latest/quickstart.html)
