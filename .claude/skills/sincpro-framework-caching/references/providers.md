# Providers, contracts and shared runs

Deeper, in the framework repo: `docs/caching/README.md` §"Providers".

## `KeyValueStore` — the six operations

`get_many`, `set`, `add` (atomic), `increment` (atomic), `delete`, `take` (atomic); `get` is
`get_many` of one key. Everything else — tag versions, single flight, idempotency claims — is built
on these, so a provider is these six and nothing more.

| Provider | Import | Install |
|---|---|---|
| `InMemoryKeyValue(now=)` | `sincpro_framework.caching` | none (standard library); one process only |
| `RedisKeyValue(client, prefix=)` | `sincpro_framework.caching.adapters.redis` | `sincpro-framework[redis]` (Valkey too; `take` needs Redis 6.2+) |
| `MemcachedKeyValue(client, prefix=)` | `sincpro_framework.caching.adapters.memcached` | `sincpro-framework[memcached]` (ttl in whole seconds) |

`RedisKeyValue`/`MemcachedKeyValue` are not exported from `sincpro_framework.caching`: the extra
is loaded only by the module that names it. The client is yours (pool, TLS, timeouts); `prefix`
lets several services share one server.

```python
import redis
from sincpro_framework.caching.adapters.redis import RedisKeyValue

STORE = RedisKeyValue(redis.Redis.from_url("redis://cache:6379/0"), prefix="billing:")
```

Build the store once per process, in the context's `infrastructure/`, and hand the same instance
to `QueryCaching`, `Cache` and `Idempotency` (each with its own `namespace`).

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
- The queue entrypoint's inbox uses the same port (`QueueOptions` from
  `sincpro_framework.entrypoints.faststream`, see `sincpro-framework-entrypoints`):
  `QueueOptions(inbox=KeyValueRecords(RedisKeyValue(...)))` across replicas, `inbox=None` off.
- A transactional `IdempotencyRecords` of yours proves itself with
  `sincpro_framework.testing.IdempotencyRecordsContract`.

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
- `ManualClock` (`sincpro_framework.testing`) — a clock for tests: `InMemoryKeyValue(now=clock.now)`,
  `Cache(now=clock.now)`, `QueryCaching(store, now=clock.now)`.
