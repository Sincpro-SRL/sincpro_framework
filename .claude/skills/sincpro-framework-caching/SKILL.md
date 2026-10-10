---
name: sincpro-framework-caching
description: Cache and deduplicate in sincpro_framework — QueryCaching (a Query's answer kept and invalidated by what it read), Cache (any value judged by freshness/validation/fail-safe), Idempotency (a write runs once per key across replicas), and the KeyValueStore providers. Use whenever a task caches an answer, makes a command idempotent, adds a retry-safe write, or wires Redis/Valkey/Memcached.
---

# sincpro-framework-caching

Caching is infrastructure decided where a bounded context is composed, like its repository or its
queue. **The use case is written exactly as without one.** Three tools share one store port:
`QueryCaching` keeps a Query's answer, `Cache` keeps any value, `Idempotency` runs a write once per
key.

## Context

- **Solves:** an expensive Query answered again and again (`QueryCaching`); a remote lookup (a
  tenant a token resolves to, a catalog another service publishes) asked on every call (`Cache`);
  a retried write — a client timeout, a broker redelivery, two replicas — that must not run twice
  (`Idempotency`).
- **Is not:** a persistence layer (a kept value may vanish at any time), an HTTP cache, a session
  store, or the in-process rows of a read for analysis (that is `QueryCache` in
  `sincpro_framework.data_layer.data_analysis`, `sincpro-framework-analytics`).
- **Do not use it** to make a Command decide from a cached answer (a Command decides from the
  database), for a read that must see its own write with no invalidation path, or for a write that
  is already idempotent by a unique constraint or natural key.

## Abstractions

| Term | What it is | Kind | Import |
|---|---|---|---|
| `KeyValueStore` | The store contract: `get_many`, `set`, `add` (atomic), `increment` (atomic), `delete`, `take` (atomic), plus `get` | port (abstract) | `sincpro_framework.data_layer.caching` |
| `InMemoryKeyValue` | Store in this process; standard library only. One replica | adapter | `sincpro_framework.data_layer.caching` |
| `RedisKeyValue(client, prefix=)` | Store on Redis or Valkey — extra `[redis]` | adapter | `sincpro_framework.data_layer.caching.adapters.redis` |
| `MemcachedKeyValue(client, prefix=)` | Store on Memcached (pymemcache) — extra `[memcached]` | adapter | `sincpro_framework.data_layer.caching.adapters.memcached` |
| `QueryCaching` | Keeps Query answers per bus, tagged by the aggregates the repository saw them read; `.on(bus, Query, policy)` installs an interceptor | registry | `sincpro_framework.data_layer.caching` |
| `CachePolicy` | One Query's `ttl`, `vary_by`, `stale_for`, `jitter`, `early_expiry`, `depends_on`, `max_bytes`, `wait_for_others` | setting | `sincpro_framework.data_layer.caching` |
| `invalidate_on_commit(database, caching)` | Invalidates every aggregate a SQL commit wrote, after the commit | function | `sincpro_framework.data_layer.orm` |
| `QueryCaching.invalidated_by({Event: [Aggregate]})` | A bus that invalidates when another process's events arrive; hand it to the `Subscriber` | function | (method) |
| `Cache` | Keeps any value by its parameters: `get_or_compute(key, compute, KeepPolicy, codec)`, `forget` | adapter | `sincpro_framework.data_layer.caching` |
| `KeepPolicy` | A `Cache` call's `freshness` + `validation` + `failure` (+ `wait_for_others`, `max_bytes`) | setting | `sincpro_framework.data_layer.caching` |
| `Freshness` / `TimeToLive` (alias `Lifetime`) / `Sliding` | How long a value is served as is | port / setting | `sincpro_framework.data_layer.caching` |
| `Validation` / `Unconditional` / `ExternalVersion` | Whether a kept value is still right (an ETag-like version) | port / setting | `sincpro_framework.data_layer.caching` |
| `FailurePolicy` / `Raise` / `FailSafe` | What a failing source answers (`FailSafe` = stale-if-error) | port / setting | `sincpro_framework.data_layer.caching` |
| `Eviction` / `Lru` / `Unbounded` | Bound of the process tier | port / adapter | `sincpro_framework.data_layer.caching` |
| `Codec` / `JsonCodec(shape)` | A value as bytes for a shared store | port / adapter | `sincpro_framework.data_layer.caching` |
| `CacheObserver` / `SpanObserver`, `MetricsObserver`, `CountingObserver`, `NoObserver`, `Observers` | Told each call's `CacheOutcome` / `IdempotencyOutcome` | port / adapter | `sincpro_framework.data_layer.caching` |
| `Idempotency` | Runs a write once per key: `@idempotency.once(...)` on a Feature/ApplicationService, or `.run(...)` by hand | decorator | `sincpro_framework.data_layer.caching` |
| `IdempotencyPolicy` | `expires_after`, `in_progress_for`, `wait_for_completion`, `vary_by` | setting | `sincpro_framework.data_layer.caching` |
| `IdempotentCommand` | Protocol: a Command with `idempotency_key()` | port (abstract) | `sincpro_framework.data_layer.caching` |
| `current_idempotency_key()` / `declares_once(cls)` / `key_of(...)` | The key of the run in progress / whether a class runs once / a hashed key | function | `sincpro_framework.data_layer.caching` |
| `IDEMPOTENCY_KEY` | Context key a transport fills with a received `Idempotency-Key` | setting | `sincpro_framework.data_layer.caching` |
| `AlreadyInProgress` / `KeyReused` / `IdempotencyError` | Refusals, `DomainError`s (409 / 422) | error | `sincpro_framework.data_layer.caching` |
| `IdempotencyRecords` / `KeyValueRecords(store)` | Where claims and answers live: yours over a database (transactional) / on a key-value store (best effort) | port / adapter | `sincpro_framework.data_layer.caching` |
| `KeyValueRuns(store)` | Crons across replicas: one claim per tick | adapter | `sincpro_framework.entrypoints.adapters.cron` |
| `KeyValueStoreContract` / `IdempotencyRecordsContract` | Test base classes a store / records of yours inherit | test contract | `sincpro_framework.runtime.testing` |
| `CodecContract` / `FreshnessContract` / `EvictionContract` | Same, for a codec / freshness / eviction | test contract | `sincpro_framework.runtime.testing` |

Look-alikes: `CachePolicy` (QueryCaching) ≠ `KeepPolicy` (Cache) ≠ `IdempotencyPolicy`.
`CachePolicy.depends_on` (a field: extra aggregates) ≠ `QueryCaching.depends_on(query)` (a method:
the tags a held answer depends on). `QueryCaching` ≠ `QueryCache` (data analysis).

## Architecture

**Inside the framework.** `sincpro_framework/data_layer/caching/`: `domain/` (the store port, strategies,
policies, refusals), `adapters/` (in-memory store, `JsonCodec`, evictions, observers,
`KeyValueRecords`, and `redis.py`/`memcached.py`, loaded only when imported), `infrastructure/`
(one caller per key, the store breaker), `entrypoint/` (`Cache`, `Idempotency`, `QueryCaching`).
Only `QueryCaching` knows repositories; `invalidate_on_commit` lives in `orm/` (`[sqlalchemy]`).
`redis` and `pymemcache` are optional extras, never core dependencies.

**Inside a consumer service** (one `UseFramework` per bounded context, created in
`infrastructure/framework.py` before `services/` is imported):

```
domains/billing/
  __init__.py                 # 1. billing = config_billing_framework("billing")
                              # 2. from . import services          (registers the handlers)
                              # 3. cache_queries(billing)          (after handlers, before the first call)
  infrastructure/
    cache.py                  # STORE = RedisKeyValue(...); caching = QueryCaching(STORE, namespace="billing",
                              #   sensitive=("user_id", "tenant_id")); idempotency = Idempotency(STORE, namespace="billing")
                              # def cache_queries(bus): caching.on(bus, QueryBalance, CachePolicy(...))
    dependencies.py           # adapters; invalidate_on_commit(database, caching) where the Database is built
    framework.py
  services/
    query_balance.py          # a plain ApplicationService: no cache code
    issue_receipt.py          # @billing.feature(Cmd) + @idempotency.once(...) (imports idempotency from infrastructure/cache.py)
  adapters/
    tenant_registry.py        # a remote client wrapping its call in Cache.get_or_compute
entrypoints/
  consumer.py                 # Subscriber(..., caching.invalidated_by({InvoiceIssued: [Invoice]}))
```

`caching.on` needs the handler already registered (else `TypeError`) and the bus not built yet
(else `BusAlreadyBuilt`). `@idempotency.once` needs the `Idempotency` instance at import time.

**The flow of one call:**

```
bus(QueryBalance)  → interceptor from caching.on
  key = bus + Query + its values + response-schema hash + context keys in vary_by
  held, tag versions unchanged, fresh      → answered, use case not run
  missing / expiring → winner of store.add runs the use case, noting what it read → kept with those tags
repository.save(Invoice) + commit → invalidate_on_commit → increment tag "Invoice" → next read recomputes
another process: InvoiceIssued → Subscriber → invalidated_by bus → increment tag

bus(CommandIssueReceipt) → once(): key = handler + Command + idempotency_key() + vary_by
  claim won (atomic add) → execute → answer kept for expires_after
  completed, same payload → answer replayed, execute not run
  other payload → KeyReused;  still running past wait_for_completion → AlreadyInProgress
  execute raised → claim released, the retry runs
```

## Mistakes an agent makes

- **A Query that reads through an adapter.** Only repository reads are noted; an answer that read
  nothing noted and declares no `depends_on` is answered and **never kept** (a log warning on every such call). Add
  `CachePolicy(depends_on=[...])`, or cache the remote call with `Cache` in the adapter.
- **Writes that never invalidate.** A `MemoryRepository`, a raw session without
  `invalidate_on_commit`, another process without `invalidated_by`, or a second `QueryCaching` in
  another `namespace`: answers stay stale until their ttl.
- **A tenant that is not in the context.** `vary_by` names **context keys only**; the
  authenticated identity is not put in the context by default, so with nothing in the context
  every tenant shares one answer. Give the key from the identity with a context provider:
  `@billing.context_provider(gives=["tenant_id"])` returning `{"tenant_id": current_identity().tenant}`
  (`references/query-caching.md`).
- **A tenant or user carried in the context but missing from `vary_by`.** One tenant's answer is
  served to another. List those keys in `QueryCaching(sensitive=...)`: a call that carries one the
  policy does not vary by is refused (`ContractViolation`), checked on every call, not at `on()`.
- **`@idempotency.once` without `vary_by="tenant_id"`** when the key is only unique per tenant:
  tenant B gets tenant A's replayed answer.
- **A Command with no `idempotency_key()` and a generated field** (`Field(default_factory=uuid4)`,
  a timestamp): every retry is a new key and the write runs again.
- **`InMemoryKeyValue` on several replicas.** Each replica has its own answers, tag versions and
  claims: an invalidation or a claim on one is unseen on the others. Use `RedisKeyValue`.
- **`in_progress_for` shorter than the longest run.** The claim expires mid-run and a duplicate runs.
- **`get_or_compute(key, compute)` with no policy.** `KeepPolicy()` defaults to
  `TimeToLive(ttl=None)`: the value is kept until forgotten or a validation rejects it.
- **`FailSafe(serve_for=...)` with default `errors=Exception`.** It serves the old value through
  a 401 or a bug, not only an outage. Name the transient errors.
- **A `Cache` key that omits a parameter** the value depends on (the tenant): one value is shared.

## QueryCaching — a Query's answer, invalidated by what it read

```python
from datetime import timedelta
from sincpro_framework.data_layer.caching import CachePolicy, QueryCaching
from sincpro_framework.common.store import InMemoryKeyValue

caching = QueryCaching(InMemoryKeyValue(), sensitive=("user_id",))
caching.on(billing, QueryBalance, CachePolicy(ttl=timedelta(minutes=5), vary_by="tenant_id"))
```

- Its `execute` must declare its return type — the answer is read back as that type.
- **Only Queries**: nothing refuses a Command, but a Command decides from the database.
- Invalidation is the mechanism, the ttl the safety net: `caching.invalidate(Invoice)` by hand,
  `invalidate_on_commit(database, caching)` on SQL, `invalidated_by(...)` across processes.
- `namespace=` isolates contexts sharing one store; `enabled = False` rules the cache out without
  recomposing; `policies()` says what is cached; `near=` adds an in-process tier still checked
  against tag versions.

## Idempotency — a write runs once per key

```python
from sincpro_framework.data_layer.caching import AlreadyInProgress, Idempotency, KeyReused

idempotency = Idempotency(InMemoryKeyValue())          # RedisKeyValue(...) on replicas
receipts.ignore_sentry_exceptions(AlreadyInProgress, KeyReused)

@receipts.feature(CommandIssueReceipt)
@idempotency.once(
    expires_after=timedelta(minutes=2),
    in_progress_for=timedelta(minutes=1),
    wait_for_completion=timedelta(seconds=5),
    vary_by="tenant_id",
)
class IssueReceipt(Feature): ...
```

- The key is the Command's `idempotency_key()`; else the transport's `Idempotency-Key` (context key
  `IDEMPOTENCY_KEY`); else the whole Command. The whole Command is still compared: same key, other
  payload ⇒ `KeyReused`. Both refusals are `DomainError`s — list them as expected.
- `once(...)` wraps the class's own `execute`; the use case still runs inside the bus (span, access
  guard, Sentry). `execute` must be synchronous and declare its return type.
- On a `KeyValueStore` "once" is best effort for a replica that dies between the side effect and
  the record. For exactly once within a database, implement `IdempotencyRecords` over it and prove
  it with `IdempotencyRecordsContract`.

## Cache — any value, judged by a policy

```python
from sincpro_framework.data_layer.caching import Cache, ExternalVersion, FailSafe, JsonCodec, KeepPolicy, TimeToLive

tenants = Cache(InMemoryKeyValue(), namespace="catalog")     # Cache() keeps objects in this process
policy = KeepPolicy[Tenant](
    freshness=TimeToLive(ttl=timedelta(minutes=5), jitter=0.1, stale_for=timedelta(minutes=1)),
    validation=ExternalVersion(registry.current_version, kept=version_of, trusted_for=timedelta(minutes=5)),
    failure=FailSafe(serve_for=timedelta(hours=1), errors=[ConnectionError, TimeoutError]),
)
value = tenants.get_or_compute(("catalog", "acme"), lambda: registry.resolve("acme"), policy, JsonCodec(Tenant))
```

- A store-backed `Cache` keeps bytes: a `Codec` is required (refused without one).
- One caller recomputes; the others serve stale or wait. An exception is never kept.
- A failing shared store opens a breaker (`bypass_for`, 30 s): answered on the process tier,
  reported `BYPASSED`. `forget` raises instead, so a missed invalidation is known.

## References

- [references/query-caching.md](references/query-caching.md) — `QueryCaching` in full
- [references/cache-and-idempotency.md](references/cache-and-idempotency.md) — `Cache` strategies and `Idempotency`
- [references/providers.md](references/providers.md) — `KeyValueStore`, providers, contracts, `KeyValueRuns`

Deep doc in the framework repo (not shipped with the package): `docs/caching/README.md`, `docs/prd/PRD_13_caching-primitives-and-idempotency.md`.

## Related

- In-process rows of a read for pandas/polars/DuckDB (`QueryCache`): `sincpro-framework-analytics`
- Crons across replicas share `KeyValueRuns`: `sincpro-framework-operations`
- `Idempotency-Key` header, queue inbox, `@idempotency.once` behind an entrypoint: `sincpro-framework-entrypoints`
- Events that invalidate (`Subscriber`): `sincpro-framework-domain-events`
