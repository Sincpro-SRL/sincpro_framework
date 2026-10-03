# Stores and propagation

## A store keeps what a flow *is*

```python
from sincpro_framework.caching.adapters.redis import RedisKeyValue
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
| `TypedCodec(schema, keep_secrets=False)` | the schema's keys, validated | the same codebase: enums, datetimes, DTOs as themselves |
| `PickleCodec()` | anything picklable | the same deployment only — it runs code when it loads |

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
