# Providers, contracts and shared runs

Depth: `docs/caching/README.md` §"Providers".

## `KeyValueStore` — the six operations

`get_many`, `set`, `add` (atomic), `increment` (atomic), `delete`, `take` (atomic), `get`.

| Provider | Install |
|---|---|
| `InMemoryKeyValue(now=)` | none (standard library) |
| `RedisKeyValue(client, prefix=)` | `sincpro-framework[redis]` (Valkey too) |
| `MemcachedKeyValue(client, prefix=)` | `sincpro-framework[memcached]` |

A store of yours proves itself with the contract the built-ins pass:

```python
from sincpro_framework.testing import KeyValueStoreContract
from sincpro_framework.caching import KeyValueStore


class TestDictStore(KeyValueStoreContract):
    def make_store(self) -> KeyValueStore:
        return DictStore()

    def expire(self, store: KeyValueStore, seconds: float) -> None:
        import time
        time.sleep(seconds)
```

A codec, a freshness or an eviction of yours proves itself the same way, with
`CodecContract`, `FreshnessContract`, `EvictionContract` (`sincpro_framework.caching.testing`).

## Records for idempotency and the queue inbox

- `IdempotencyRecords` / `KeyValueRecords(store)` — where an idempotency claim and answer live.
- The queue's inbox (`docs/entrypoints/queue.md`) uses the same port:
  `QueueOptions(inbox=KeyValueRecords(RedisKeyValue(...)))` across replicas, `inbox=None` off.

On a key-value store, "once" is best effort for a replica that dies between the use case and
`complete`. On records that commit inside the use case's own transaction, effects are exactly once
within that database.

## `KeyValueRuns` — crons across replicas

The record several cron replicas share: one of them runs each tick.

```python
from sincpro_framework.caching import InMemoryKeyValue
from sincpro_framework.cron import KeyValueRuns

shared = InMemoryKeyValue()                   # RedisKeyValue(...) on real replicas
one, other = KeyValueRuns(shared), KeyValueRuns(shared)
tick = datetime(2026, 9, 27, 2, 0, tzinfo=UTC)
assert one.claim("close-books", tick) and not other.claim("close-books", tick)
```

## Not to be confused with

- `QueryCache` (`sincpro_framework.data_analysis`) — the rows of a read held **in one process** for
  analysis (pandas/polars/DuckDB). `QueryCaching` is a Query's answer kept for its bus across
  replicas. Different things.
