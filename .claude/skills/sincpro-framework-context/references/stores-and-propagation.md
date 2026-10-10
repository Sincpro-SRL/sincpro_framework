# Stores and propagation

## The context shared across services — the same tree, kept in a store

```python
store = KeyValueContexts(RedisKeyValue(redis.Redis.from_url(url)))   # the same in every service
sales.context_store(store)        # service A — explicit, per bus; nothing shares by default
billing.context_store(store)      # service B — another project too

# A: an ApplicationService
self.context["discount"] = 10     # the API never changes
# B: a Feature reached through a queue or the remote bus, in that execution's chain
self.context.get("discount")      # → 10, read from the store — even if A wrote it after handing on
use_context().at(Level.GLOBAL).set("maintenance", True)   # every service that set the store sees it
```

- Each node of an execution (entrypoint, application, feature, hook, scope) is kept under its own
  id, for `ttl` (default 24 h): concurrent requests never collide.
- Only an id travels (`x-context-node`, `sincpro.context_node` in the remote bus's context); the
  receiver opens under the sender's chain read back from the store — read-only there, its own
  writes stay on its own nodes.
- The process is kept per service (`process:<service_name>`), never read by another service;
  `Level.GLOBAL` (`global`) is the one level every service shares.
- Others' nodes, the process and the global are read again at most once per `every` (default 1 s).
- Plain JSON codec by default — projects need no classes in common; `TypedCodec` to get types back.
- Full design: `docs/prd/PRD_24_shared-context.md`.

## A store keeps what a flow *is*

```python
from sincpro_framework.data_layer.caching.adapters.redis import RedisKeyValue
from sincpro_framework.context import ContextStore, KeyValueContexts, TypedCodec, use_context

contexts = KeyValueContexts(RedisKeyValue(redis.Redis.from_url(url)))   # [redis] extra
use_context().root.set(ContextStore, contexts)        # the process
# or: billing.context_store(contexts)                 # one bus
# or: bus.context(..., store=contexts)                # one block — the nearest wins

contexts.keep("tenant:acme", {"lang": "es", "plan": "pro"})
contexts.keep(f"session:{sid}", {"user_id": "ana", "tenant_id": "acme"}, ttl=timedelta(hours=8))

with bus.context({"channel": "pos"}, restore=["tenant:acme", f"session:{sid}"]):   # in order
    bus(CommandX())

with bus.context(values, keep_as=f"flow:{sale_id}", ttl=timedelta(hours=1)):      # kept with its chain
    bus(CommandStartSale())
with bus.context(restore=f"flow:{sale_id}"):                                      # a worker resumes it
    bus(CommandContinueSale())
```

| Adapter | For |
|---|---|
| `InMemoryContexts()` | tests, one process |
| `KeyValueContexts(store, codec)` | every replica and service over `KeyValueStore`: Redis, Valkey, Memcached, a project's own |

| Codec | Writes | Back |
|---|---|---|
| `PlainCodec()` (default) | the travelling keys as JSON | any language; lists stay lists |
| `TypedCodec(schema)` | the schema's keys, validated | the same codebase: enums, datetimes, DTOs as themselves |
| `PickleCodec()` | anything picklable, each value on its own — a reader lacking a value's class gets the context without that key (a warning), never an error for the whole | the same deployment only — it runs code when it loads |

A store is **not a cache**: it keeps what a flow is (a session, a tenant's defaults, a flow to
resume), written by the flow. Each `keep` moves the key's version.

## The process level across replicas

```python
use_context().root.share(contexts, key="process", every=timedelta(seconds=5))
use_context().root.set("maintenance", True)       # kept; every replica reads it within 5 s
```

Read locally; at most once per `every` the version is asked and the values re-read only when it
moved. Values under a non-text key (a store, settings) stay local.

## Across a transport

| Transport | How |
|---|---|
| a bus calling another, one process | the whole tree |
| `remote_execution` (HTTP/gRPC) | the caller's whole context packed in one header, caused by the calling execution |
| a broker (FastStream), REST, JSON-RPC, gRPC | `inject(context)` / `extract(headers)`: `baggage` (W3C, scalars), `sincpro-context` (JSON, lists), `x-correlation-id` · `x-causation-id` · `x-execution-id` |
| the frontend | `use_context().to_client()` out; the `sincpro-context` header back — every HTTP entrance reads it |

```python
from sincpro_framework.context.adapters.propagation import extract, inject

headers = inject(use_context())          # inside the execution that sends: it becomes the cause
context = extract(request.headers)       # what an entrance opens its flow with
```

Only text, numbers, booleans and lists of them travel; objects and `Secret`s stay, warned once.
