---
name: sincpro-framework-context
description: Read, scope, carry and keep the execution context of a sincpro_framework service — use_context() from anywhere, the tree of levels (ROOT, BUS, ENTRYPOINT, APPLICATION, FEATURE, HOOK, SCOPE), bus.context(...) and self.context, set vs mapping writes, threads and pools (ContextExecutor, ContextThread, in_context), context_provider, context_schema, requires_context, Secret, stores (InMemoryContexts, KeyValueContexts over Redis; keep_as/restore; root.share across replicas), propagation (inject/extract, baggage, sincpro-context) and the execution identity (execution_id, causation_id, correlation_id). Use whenever a task reads or passes tenant/user/lang/correlation, hands work to a thread, derives credentials per tenant, keeps a session or a flow, shares settings across replicas, or carries context to another service, a broker or the frontend.
---

# sincpro-framework-context

The context says who a call acts for, for which tenant, in which language and in which flow — and
anything a project adds (`TOKEN`, `SIAT_ENV`, `nit_id`). It reaches every Feature, ApplicationService,
hook, interceptor and adapter without a parameter, crosses threads, buses, services and brokers, and
can be kept in a store for whoever resumes the flow.

## Context

- **Solves:** metadata every use case needs and no DTO should carry; per-tenant credentials derived
  once instead of rebuilt at every call site; a flow followed across services by one id; a session or
  a flow kept and resumed; defaults shared by every replica.
- **Is not:** a cache (a cache keeps what can be recomputed; a store keeps what a flow *is*), a place
  for business data (that is a DTO or an aggregate), or a way to pass results between use cases (an
  answer is a Response).
- **Do not put in it:** records, connections, an Odoo `env` — they cannot travel. Keep them in
  dependencies (`add_dependency`) and put their key (`nit_id`) in the context.

## Abstractions

| Term | What it is | Kind | Import |
|---|---|---|---|
| `use_context(level=None)` | The context in play, from anywhere — a `dict` of what its node sees; with a level, that level's node or `None` | function | `from sincpro_framework.context import use_context` |
| `Context` | That object: `dict` reads, navigation (`.level`, `.parent`, `.root`, `.bus`, `.entrypoint`, `.application`, `.feature`, `.hook`, `.at(level)`, `.own`, `.origin(key)`, `.lineage()`, `.execution`), writes (`[]=` / `.set`), `.scoped(...)`, `.keep`, `.restored`, `.to_client()`, `.share` (root only) | DTO | `sincpro_framework.context` |
| `Level` | `ROOT · BUS · ENTRYPOINT · APPLICATION · FEATURE · HOOK · SCOPE` | setting (StrEnum) | `sincpro_framework.context` |
| `EntrypointKind` | `DIRECT · REST · RPC · GRPC · MCP · QUEUE · CRON · REMOTE` — which entrance opened the flow | setting (StrEnum) | `sincpro_framework.context` |
| `Origin` | `(level, label, execution_id)` — who said a value | DTO | `sincpro_framework.context` |
| `bus.context(values, global_scope, kind, restore, keep_as, ttl, store)` | Opens a scope of the bus for a `with` block (the first of a flow is its ENTRYPOINT); `global_scope=True` writes the bus's own node | function | method of `UseFramework` |
| `self.context` | The same `Context`, in a handler | DTO | attribute of `Feature` / `ApplicationService` |
| `bus.current_context()` | The same `Context`, read-only | function | method of `UseFramework` |
| `ContextExecutor` / `ContextThread` / `in_context(fn)` | A pool, a thread, a wrapped function that start in the submitter's context | adapter | `sincpro_framework.context` |
| `bus.context_provider(needs, gives)` | A function that derives keys when an execution opens and lacks them — once per scope | decorator | method of `UseFramework` |
| `bus.context_schema(TypedDict or DTO)` | Validates and types the keys it names when a scope opens | function | method of `UseFramework` |
| `requires_context(*keys)` | Declares keys a use case cannot run without; `ContextRequired` before the handler when missing | decorator | `sincpro_framework.context` |
| `ContextRequired` | Raised for a declared key that is missing | DTO | `sincpro_framework.exceptions` |
| `Secret` | pydantic's: masked on every signal, never travels, `.get_secret_value()` where it is used | DTO | `from pydantic import Secret` |
| `ContextStore` | Port: `keep(key, values, ttl)`, `restore(key)`, `forget(key)`, `version(key)` | port | `sincpro_framework.context` |
| `InMemoryContexts` / `KeyValueContexts(store, codec)` | One process / every replica over any `KeyValueStore` (Redis, Valkey, Memcached) | adapter | `sincpro_framework.context` |
| `PlainCodec` / `TypedCodec(schema, keep_secrets)` / `PickleCodec` | How a store writes a context: any language / types back / anything picklable | adapter | `sincpro_framework.context` |
| `bus.context_store(store)` | The store this bus's scopes keep and restore with | function | method of `UseFramework` |
| `inject(context)` / `extract(headers)` | The context as text headers and back: `baggage`, `sincpro-context`, `x-correlation-id`/`x-causation-id`/`x-execution-id` | function | `sincpro_framework.context.adapters.propagation` |
| `Execution` / `current_execution()` | `execution_id`, `causation_id`, `correlation_id`, `use_case`, `bus`, `level`, `started_at` | DTO / function | `sincpro_framework.context` |
| `bus.execution_ids(fn)` | Mint execution ids with another generator (ULID, UUID v4) | function | method of `UseFramework` |
| `carrying(values, kind)` | Opens a flow for code that is not a bus (a worker) | function | `sincpro_framework.context` |
| `USER_ID` · `TENANT_ID` · `TENANT_IDS` | The standard keys: `user_id`, `tenant_id`, `tenant_ids` | setting | `sincpro_framework.context` |

Look-alikes: `context[key] = v` (the scope of the call — siblings see it) ≠ `context.set(key, v)` (this
node — only what it runs). `bus.context(...)` **opens** a scope; `use_context()` / `self.context`
**read** it. A store (`ContextStore`) ≠ a cache (`caching`). `inject`/`extract` (text headers) ≠
`remote_execution` (the whole context packed, same codebase).

## Architecture

**In the framework** (`sincpro_framework/context/`, standard library + pydantic, no extra):

```text
domain/          level.py (Level, EntrypointKind, Origin) · node.py (ContextNode, Values — copy-on-write)
                 execution.py (Execution, chained, the identity keys) · keys.py (standard keys)
                 store.py (ContextStore) · codec.py (ContextCodec)
infrastructure/  tree.py (the node in play: a ContextVar; ROOT; opened_execution; carrying)
                 threads.py · providers.py · shared.py (root shared by replicas) · thread_context_bus.py
adapters/        stores.py (InMemoryContexts, KeyValueContexts) · codecs.py · propagation.py
entrypoint/      facade.py (Context, use_context) · bus.py (ContextMixin, FrameworkContext) · consumer.py
```

`sincpro_framework.context` exports the public names lazily: importing it never imports a bus.

**The tree:**

```text
ROOT         the process: defaults, flags, settings under their type, a store
 └─ BUS       what a bounded context publishes (global_scope=True, context_store)
     └─ ENTRYPOINT   what the entrance knew + its EntrypointKind (opened by REST/RPC/gRPC/MCP/queue/cron/remote, or DIRECT)
         └─ APPLICATION / FEATURE   each execution, a node of its own, with its Execution
             └─ HOOK                each run of a repository's hook
             SCOPE                  a with-block, anywhere
```

Reading walks up; the nearest node wins. `contextvars` holds which node is current per thread and
task; nodes are immutable mappings replaced whole (copy-on-write), so threads never tear them.

**In a consumer service:**

```text
domains/<ctx>/infrastructure/framework.py    bus = UseFramework(...); bus.context_schema(MyContext)
domains/<ctx>/infrastructure/context.py      @bus.context_provider(...) functions; bus.context_store(...)
domains/<ctx>/services/*.py                  read self.context / use_context(); @requires_context where a key is vital
domains/<ctx>/adapters/*.py                  read use_context() (tenant, credentials) — never write
entrypoints/*                                gateways open the ENTRYPOINT themselves; a worker uses carrying(...)
```

## Mistakes an agent makes

- **Holding a `Context` across a change.** It is the context *when it was asked for*. Call
  `use_context()` / `self.context` again after a `set`, a scope or a provider changed it.
- **`context.set` expecting a sibling to see it.** `set` reaches what this node runs. For every
  execution of the call write `context[key] = value`; for the whole flow `context.entrypoint.set`.
- **Writing outside every scope.** It writes the process (ROOT), with a warning. Open
  `bus.context(...)` or `use_context().scoped(...)` first.
- **A bare `ThreadPoolExecutor` / `threading.Thread`.** Workers start with no context on the
  default build. Use `ContextExecutor`, `ContextThread` or `in_context(fn)`.
- **Objects in the context to "pass them along".** A record, an `env`, a client cannot travel and is
  dropped from every header with a warning. Put the id in the context and the object in a dependency;
  derive what is needed with a `context_provider`.
- **Rebuilding credentials at every call site.** Register one `context_provider(needs=["nit_id"],
  gives=["TOKEN", "SIAT_ENV"])` and open `bus.context({"nit_id": ...})`.
- **A missing tenant falling back silently.** Declare `@requires_context("TOKEN")` on the use case
  that must never run under a default; nothing else is checked.
- **A secret as a plain string.** Wrap it: `Secret(token)`. It is masked in logs, spans and errors,
  dropped from headers and `to_client()`.
- **`"user.id"` / `"tenant"`.** Read as `user_id` / `tenant_id` with a warning — write the standard
  keys. Use `tenant_ids` (a list) for an execution acting across tenants.
- **Restoring with no store.** `restore=`/`keep_as=` find the nearest `ContextStore` — set one with
  `use_context().root.set(ContextStore, store)`, `bus.context_store(store)` or `store=`.
- **Expecting `bus.current_context()` to be writable.** It is read-only by contract.

## The rule that shapes everything

**One object, wherever it is reached; the nearest node wins.** Open scopes where the information is
known (an entrance, a block), read it where it is needed, and never pass it as a parameter.

```python
from pydantic import Secret

from sincpro_framework import Feature, UseFramework
from sincpro_framework.context import requires_context

siat = UseFramework("siat")
siat.context_schema(SIATContext)                      # SIAT_ENV=2 arrives SIATEnvironment.TEST


@siat.context_provider(needs=["nit_id"], gives=["TOKEN", "SIAT_ENV"])
def credentials(context):
    nit = nits.get(context["nit_id"])                 # a dependency, not a context value
    return {"TOKEN": Secret(nit.token), "SIAT_ENV": nit.environment}


@siat.feature(CommandSendInvoice)
@requires_context("TOKEN", "SIAT_ENV")
class SendInvoice(Feature):
    def execute(self, dto: CommandSendInvoice) -> ResponseSent:
        token = self.context["TOKEN"].get_secret_value()
        ...


with siat.context({"nit_id": nit.id}):                # the caller says which tenant, nothing else
    siat(CommandSendInvoice(...))
```

## The rules that matter

- **Open, don't pass.** `bus.context({...})` at the edge; handlers and adapters read
  `self.context` / `use_context()`.
- **Two writes.** `context[key] = value` → the scope of the call. `context.set(key, value)` → this
  node only. `entrypoint.set` / `root.set` → said on purpose.
- **Levels by name** when it matters: `use_context(Level.ENTRYPOINT)["user_id"]` is who asked, even
  if a lower level changed it; `context.origin(key)` says who changed it.
- **Values under their type** — settings, clients, a store: `root.set(SiatSettings, settings)`,
  `use_context()[SiatSettings]`. Only text keys travel.
- **Threads:** `ContextExecutor` / `ContextThread` / `in_context`. `asyncio` needs nothing.
- **Stores keep what a flow is:** `keep_as="sale-77"` / `restore="sale-77"`, `restore=["tenant:acme",
  f"session:{sid}"]` (in order, the last wins), `ttl=` always for sessions. `KeyValueContexts` over
  Redis for replicas; `TypedCodec(schema)` to get the types back in the same codebase.
- **The process across replicas:** `use_context().root.share(store, every=timedelta(seconds=5))` —
  read locally, re-read when the version moved.
- **Across services:** buses in one process share the tree; `remote_execution` sends the whole
  context; brokers and HTTP/gRPC use `inject`/`extract` (`baggage` + `sincpro-context` + identity);
  the frontend gets `to_client()` and sends `sincpro-context` back.
- **Identity is free:** every execution has `execution_id` / `causation_id` / `correlation_id` on
  every signal; events recorded, published or saved inside an execution join its chain.

## References

- [references/levels-and-writes.md](references/levels-and-writes.md) — the tree, navigation, the two writes, scopes, values by type, the standard keys
- [references/threads.md](references/threads.md) — pools, threads, async, why the framework hands it on
- [references/providers-and-requirements.md](references/providers-and-requirements.md) — `context_provider`, `context_schema`, `requires_context`, `Secret`
- [references/stores-and-propagation.md](references/stores-and-propagation.md) — `ContextStore`, codecs, `keep_as`/`restore`, `root.share`, `inject`/`extract`, the frontend

Deep docs in the framework repo: `docs/core/context-manager.md` (runs as a test),
`docs/prd/PRD_22_context-component.md`, `docs/prd/PRD_21_execution-identity.md`.

## Related

- Interceptors, error handlers, `replaces=`: `sincpro-framework-core`
- Entrypoints that open the flow (REST, RPC, gRPC, MCP, queue, context map): `sincpro-framework-entrypoints`
- Settings shapes and `Secret`: `sincpro-framework-settings`
- Events joining the chain, brokers: `sincpro-framework-domain-events`
- Logs, spans, metrics reading the context: `sincpro-framework-observability`
- `KeyValueStore` adapters (Redis, Valkey, Memcached): `sincpro-framework-caching`
