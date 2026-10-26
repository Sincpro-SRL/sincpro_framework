# Stores and propagation

## The context shared across services — the same tree, persisted in a store

```python
store_context_in(RedisKeyValue(redis.Redis.from_url(url)))   # the process: process level + global
store = KeyValueContexts(RedisKeyValue(redis.Redis.from_url(billing_url)))   # billing's own Redis
sales.store_context_in(store)        # service A — explicit, per bus; nothing shares by default
billing.store_context_in(store)      # service B — another project too

# A: an ApplicationService
self.context["discount"] = 10     # the API never changes
# B: a Feature reached through a queue or the remote bus, in that execution's chain
self.context.get("discount")      # → 10, read from the store — even if A wrote it after handing on
use_context().at(ContextLevel.GLOBAL).set("maintenance", True)   # every service that set the process store sees it
```

- Each node of an execution (entrypoint, application, feature, hook, scope) is stored under its own
  id, for `ttl` (default 24 h): concurrent requests never collide.
- The flow travels as the headers' snapshot and as the id of the node in play (`x-context-node`,
  `sincpro.context_node` in the remote bus's context); a receiver on the same store opens under the
  sender's chain read back from it — live, read-only there, its own writes stay on its own nodes.
- Each level lives where its owner said: the process (`process:<service_name>`) and
  `ContextLevel.GLOBAL` (`global`) on the process's store (`store_context_in`), joined at the first
  execution of any bus; a bounded context's level (`bus:<name>`) and its execution nodes on its own
  store (`bus.store_context_in`) — a bounded context with its own Redis never holds the process.
- The bounded context's level is read by every pod of it: `global_scope=True` /
  `use_context().bus.set(...)` in one pod reaches all; a `global_scope` block puts back only its own
  keys when it ends.
- Others' nodes, the bounded context's level, the process and the global are read again at most
  once per `every` (default 1 s).
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

## When the store is down

The store syncs the replicas; reads are always this process's memory. Down, every call goes on:
joining never fails; the shared levels serve what they read last and queue their writes, sent in
order on the latest values when it answers (same key: whoever sent last); execution nodes stay
local and hand-offs travel by their headers' snapshot; `bus.cache` bypasses. Drafts, pipeline
runs, idempotency and leases **fail** (`StoreUnavailable`): they cannot pretend to persist. After a
failure the store is not asked for 5 s (`OUTAGE_BYPASS`, a `StoreBypass` of the process — never the
circuit breaker, whose state lives in the store). Said once down, once back. Keep the client's
timeouts short (`socket_timeout`, no retries).

## Across a transport — only the flow crosses

| Level | Owner | Lives on | Crosses |
|---|---|---|---|
| global | the organization | the process store | never |
| process | the service | the process store | never |
| bus | the bounded context | its own store | never |
| entrypoint → application → feature → scope | the flow | the bounded context's store | always |
| hook of a reading | the hook | this process | never |

`use_context().flow()` is what crosses; `inject`, `to_client()`, a published message's envelope
and the remote bus carry exactly that. A flow value wins on the other side over its own levels; a
key the flow never said is read from the receiver's own. A plain mapping `inject({...})` is carried
as given.

| Transport | How |
|---|---|
| a bus calling another, one process | the whole tree |
| `remote_execution` (HTTP/gRPC) | the caller's flow packed in one header, caused by the calling execution |
| a broker (FastStream), REST, JSON-RPC, gRPC | `context_propagation.inject(use_context())` / `context_propagation.extract(headers)` |
| the frontend | `use_context().to_client()` out; the `sincpro-context` header back — every HTTP entrance reads it |

| Header | Carries |
|---|---|
| `x-correlation-id` · `x-causation-id` · `x-execution-id` | the identity |
| `sincpro-context` | the flow's travelling keys as JSON — the snapshot |
| `baggage` | the flow's scalars, W3C |
| `x-context-node` | the node in play, when its bus stores its context — read live by a receiver on the same store |

| Receiver | Reads the flow | Written after the hand-off |
|---|---|---|
| same store as the sender | live, by `x-context-node` | yes |
| a store of its own (one Redis per bounded context) | the `sincpro-context` snapshot | no |
| only the ids | the identity only | no |

```python
from sincpro_framework.context import context_propagation

headers = context_propagation.inject(use_context())      # inside the execution that sends: it becomes the cause
headers = context_propagation.inject()                   # the same: the flow in play
context = context_propagation.extract(request.headers)   # what an entrance opens its flow with
```

Only text, numbers, booleans, lists and one-level mappings of them travel, a `Secret` as its value;
objects stay, warned once. Past 4 KB (`CONTEXT_BYTES`) the headers are warned about, naming the
largest key: a large value goes in a store and its id in the context. Cache, idempotency, drafts,
pipeline runs and leases never travel — they live in the store, keyed by what the context says.
