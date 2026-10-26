# Stores and propagation

## The context shared across services — the same tree, persisted in a store

```python
store = KeyValueContexts(RedisKeyValue(redis.Redis.from_url(url)))   # the same in every service
sales.store_context_in(store)        # service A — explicit, per bus; nothing shares by default
billing.store_context_in(store)      # service B — another project too

# A: an ApplicationService
self.context["discount"] = 10     # the API never changes
# B: a Feature reached through a queue or the remote bus, in that execution's chain
self.context.get("discount")      # → 10, read from the store — even if A wrote it after handing on
use_context().at(ContextLevel.GLOBAL).set("maintenance", True)   # every service that set the store sees it
```

- Each node of an execution (entrypoint, application, feature, hook, scope) is stored under its own
  id, for `ttl` (default 24 h): concurrent requests never collide.
- Only an id travels (`x-context-node`, `sincpro.context_node` in the remote bus's context); the
  receiver opens under the sender's chain read back from the store — read-only there, its own
  writes stay on its own nodes.
- The process is stored per service (`process:<service_name>`), never read by another service;
  `ContextLevel.GLOBAL` (`global`) is the one level every service shares.
- Others' nodes, the process and the global are read again at most once per `every` (default 1 s).
- Plain JSON codec by default — projects need no classes in common; `TypedCodec` to get types back.
- Full design: `docs/prd/PRD_24_shared-context.md`.

## Where the context is stored

The context reaches another process by itself — every event, queue message, remote call and answer
to the frontend carries it, and the entrance opens its flow with it. Nothing is saved or loaded by
name. The store holds what the framework stores for the context: drafts, pipeline runs, leases,
circuit breakers, every bus's cache and idempotency.

```python
from sincpro_framework.context import KeyValueContexts, TypedCodec, store_context_in
from sincpro_framework.data_layer.store import RedisKeyValue

contexts = store_context_in(RedisKeyValue(redis.Redis.from_url(url)))   # the process, [redis] extra
billing.store_context_in(KeyValueContexts(RedisKeyValue(own)))           # or one bus, its own store
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

A store is **not a cache**: what it persists is written by the flow — a draft, a pipeline run. Each
`save` moves the key's version.

## The process level across replicas

```python
use_context().root.share(contexts, "process:billing", every=timedelta(seconds=5))
use_context().root.set("maintenance", True)       # stored; every replica reads it within 5 s
```

Read locally; at most once per `every` the version is asked and the values re-read only when it
moved. The key names the service (`process:<service>`, as a sharing bus keys it): two
services on one key share one level. A write lands on the latest values, a default the code
seeds is added when the store lacks it, and a value that does not travel (a store, settings)
stays local.

## Across a transport

| Transport | How |
|---|---|
| a bus calling another, one process | the whole tree |
| `remote_execution` (HTTP/gRPC) | the caller's whole context packed in one header, caused by the calling execution |
| a broker (FastStream), REST, JSON-RPC, gRPC | `context_propagation.inject(context)` / `context_propagation.extract(headers)`: `baggage` (W3C, scalars), `sincpro-context` (JSON, lists), `x-correlation-id` · `x-causation-id` · `x-execution-id` |
| the frontend | `use_context().to_client()` out; the `sincpro-context` header back — every HTTP entrance reads it |

```python
from sincpro_framework.context import context_propagation

headers = context_propagation.inject(use_context())      # inside the execution that sends: it becomes the cause
context = context_propagation.extract(request.headers)   # what an entrance opens its flow with
```

Only text, numbers, booleans and lists of them travel; objects and `Secret`s stay, warned once.
