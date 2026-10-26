---
name: sincpro-framework-caching
description: Cache and deduplicate in sincpro_framework — bus.cache / bus.idempotency (facades stored where the context is stored, nothing built), QueryCaching (a Query's answer cached and invalidated by what it read), Cache (any value judged by freshness/validation/fail-safe), Idempotency (a write runs once per key across replicas), and the KeyValueStore providers. Use whenever a task caches an answer, makes a command idempotent, adds a retry-safe write, or wires Redis/Valkey/Memcached.
---

# sincpro-framework-caching

Caching is infrastructure decided where a bounded context is composed, like its repository or its
queue. **The use case is written exactly as without one.** Every bus has two facades, stored where
its context is stored with nothing built: `billing.cache` (a function's answer, any strategy, a
Query's answer with `.queries(Query)`) and `billing.idempotency` (a write once per request,
`.exactly_once(records)` in the write's transaction); `cache` / `idempotency` are the service's.
Underneath, three tools share one store port: `QueryCaching` caches a Query's answer, `Cache` caches
any value, `Idempotency` runs a write once per key — built by hand when a store of its own is meant.

## Context

- **Solves:** an expensive Query answered again and again (`QueryCaching`); a remote lookup (a
  tenant a token resolves to, a catalog another service publishes) asked on every call (`Cache`);
  a retried write — a client timeout, a broker redelivery, two replicas — that must not run twice
  (`Idempotency`).
- **Is not:** a persistence layer (a cached value may vanish at any time), an HTTP cache, a session
  store, or the in-process rows of a read for analysis (that is `QueryCache` in
  `sincpro_framework.data_layer.data_analysis`, `sincpro-framework-analytics`).
- **Do not use it** to make a Command decide from a cached answer (a Command decides from the
  database), for a read that must see its own write with no invalidation path, or for a write that
  is already idempotent by a unique constraint or natural key.

## Abstractions

| Term | What it is | Kind | Import |
|---|---|---|---|
| `KeyValueStore` | The store contract: `get_many`, `set`, `add` (atomic), `increment` (atomic), `delete`, `take` (atomic), plus `get`; `replace` (compare-and-write, for leases) | protocol | `sincpro_framework.data_layer.store` |
| `Leases(store)` | A name held by one owner at a time across replicas: `held(name, owner, ttl)` renews in the background, `state` FREE/HELD/EXPIRED, a fence per take; `LeaseTaken`, `LeaseLost` | class | `sincpro_framework.data_layer.store` |
| `InMemoryKeyValue` | Store in this process; standard library only. One replica | adapter | `sincpro_framework.data_layer.store` |
| `RedisKeyValue(client, prefix=)` | Store on Redis or Valkey — extra `[redis]` | adapter | `sincpro_framework.data_layer.store` |
| `MemcachedKeyValue(client, prefix=)` | Store on Memcached (pymemcache) — extra `[memcached]` | adapter | `sincpro_framework.data_layer.store` |
| `DynamoKeyValue(table, client=, prefix=)` | Store on an Amazon DynamoDB table (key `key`, TTL on `expires_at`) — extra `[aws]`; contexts, drafts, pipeline runs, cron ticks and idempotency all run on it | adapter | `sincpro_framework.data_layer.store` |
| `QueryCaching` | Caches Query answers per bus, tagged by the aggregates the repository saw them read; `.on(bus, Query, policy)` installs an interceptor | registry | `sincpro_framework.data_layer.caching` |
| `QueryCachePolicy` | One Query's `ttl`, `vary_by`, `stale_for`, `jitter`, `early_expiry`, `depends_on`, `max_bytes`, `wait_for_others` | setting | `sincpro_framework.data_layer.caching` |
| `invalidate_on_commit(database, caching)` | Invalidates every aggregate a SQL commit wrote, after the commit | function | `sincpro_framework.data_layer.orm` |
| `QueryCaching.invalidated_by({Event: [Aggregate]})` | A bus that invalidates when another process's events arrive; hand it to the `Subscriber` | function | (method) |
| `Cache` | Caches any value by its parameters: `get_or_compute(key, compute, CachePolicy, codec)`, `forget` | adapter | `sincpro_framework.data_layer.caching` |
| `bus.cache` (`CacheFacade`) | A bounded context's cache, nothing built: `@billing.cache` (a minute, 10 s stale, ≤1 s wait), `.ttl(ttl, stale_for, jitter)`, `.sliding(idle_for, at_most)`, `.versioned(current, version_of, trusted_for)`, `billing.cache(CachePolicy(...))`; `fail_safe=` and `vary_by=` on every form; key = bus name + function + arguments (`self` left out) + whose it is (`tenant_id`, `tenant_ids`, `user_id` of the context, empty when unsaid); `.forget(args)`; each configured cache (`ConfiguredCache`) is also `.get(key, compute, codec)` / `.forget(key)` by hand — what an adapter handed `billing.cache` uses | facade / decorator | `sincpro_framework.data_layer.caching` |
| `bus.cache.queries(Query, QueryCachePolicy)` | The bounded context's `QueryCaching`, on its store under its name (a minute by default); `bus.cache.queries()` answers it — for `invalidate_on_commit` | facade | (on the bus) |
| `bus.idempotency` (`IdempotencyFacade`) | `@billing.idempotency` on a Feature/ApplicationService: once per request, 24 h, a duplicate in flight refused at once; `expires_after=`, `in_progress_for=`, `wait_for_completion=`, `vary_by=`; `.exactly_once(records)`; `.run(...)` by hand; `.policies()` | facade / decorator | `sincpro_framework.data_layer.caching` |
| `cache` / `idempotency` / `store_context_in(store)` | The service's own facades (keys under `global`); the process's store in one line — a `KeyValueStore` or a `ContextStore` | facade / function | `sincpro_framework` |
| `bus.store()` | Where the bus writes: its `store_context_in(...)`, else the process's, else memory | method | (on the bus) |
| `CachePolicy` | A `Cache` call's `freshness` + `validation` + `failure` (+ `wait_for_others`, `max_bytes`) | setting | `sincpro_framework.data_layer.caching` |
| `Freshness` / `TimeToLive` / `Sliding` | How long a value is served as is | port / setting | `sincpro_framework.data_layer.caching` |
| `Validation` / `Unconditional` / `ExternalVersion` | Whether a cached value is still right (an ETag-like version) | port / setting | `sincpro_framework.data_layer.caching` |
| `FailurePolicy` / `Raise` / `FailSafe` | What a failing source answers (`FailSafe` = stale-if-error) | port / setting | `sincpro_framework.data_layer.caching` |
| `Eviction` / `Lru` / `Unbounded` | Bound of the process tier | port / adapter | `sincpro_framework.data_layer.caching` |
| `Codec` / `JsonCodec(shape)` | A value as bytes for a shared store | port / adapter | `sincpro_framework.data_layer.caching` |
| `CacheObserver` / `SpanObserver`, `MetricsObserver`, `CountingObserver`, `NoObserver`, `Observers` | Told each call's `CacheOutcome` / `IdempotencyOutcome` | port / adapter | `sincpro_framework.data_layer.caching` |
| `Idempotency` | Runs a write once per key: `@idempotency.once(...)` on a Feature/ApplicationService, or `.run(...)` by hand | decorator | `sincpro_framework.data_layer.caching` |
| `IdempotencyPolicy` | `expires_after`, `in_progress_for`, `wait_for_completion`, `vary_by` | setting | `sincpro_framework.data_layer.caching` |
| `IdempotentCommand` | Protocol: a Command with `idempotency_key()` | port (abstract) | `sincpro_framework.data_layer.caching` |
| `Idempotency.current_key()` / `Idempotency.declared_by(cls)` / `KeyEncoder.key_of(...)` | The key of the run in progress / whether a class runs once / a hashed key | method | `sincpro_framework.data_layer.caching` |
| `IDEMPOTENCY_KEY` | Context key a transport fills with a received `Idempotency-Key` | setting | `sincpro_framework.data_layer.caching` |
| `AlreadyInProgress` / `KeyReused` / `ClientError` | Refusals, `DomainError`s (409 / 422) | error | `sincpro_framework.data_layer.caching` |
| `IdempotencyRecords` / `KeyValueRecords(store)` | Where claims and answers live: yours over a database (transactional, `transaction()`) / on a key-value store (best effort) | port / adapter | `sincpro_framework.data_layer.caching` |
| `DatabaseIdempotencyRecords(database, table)` / `template_table.idempotency_table(name, metadata)` | Exactly once: the claim commits on its own, the answer in the write's unit of work — `@bus.idempotency.exactly_once(records)` | adapter / table | `sincpro_framework.data_layer.orm` |
| `KeyValueRuns(store)` | Crons across replicas: one claim per tick | adapter | `sincpro_framework.entrypoints.adapters.cron` |
| `KeyValueStoreContract` / `IdempotencyRecordsContract` | Test base classes a store / records of yours inherit | test contract | `sincpro_framework.runtime.testing` |
| `CodecContract` / `FreshnessContract` / `EvictionContract` | Same, for a codec / freshness / eviction | test contract | `sincpro_framework.runtime.testing` |

Look-alikes: `QueryCachePolicy` (QueryCaching) ≠ `CachePolicy` (Cache) ≠ `IdempotencyPolicy`.
`QueryCachePolicy.depends_on` (a field: extra aggregates) ≠ `QueryCaching.depends_on(query)` (a method:
the tags a held answer depends on). `QueryCaching` ≠ `QueryCache` (data analysis).

## Architecture

**Inside the framework.** The store is its own package, `sincpro_framework/data_layer/store/`:
`domain/` (`KeyValueStore`, the protocol every engine satisfies; `Lease`, `LeaseState`, the store
errors), `adapters/` (one engine each: in-memory, Redis/Valkey, Memcached, DynamoDB — each loads
its library only when used), `services/` (`Leases`, and `StoreBypass`, the one breaker of a store
that does not answer, used by `Cache` and `CircuitBreaker`). Caching is built on it,
`sincpro_framework/data_layer/caching/`: `domain/` (strategies, policies, refusals), `adapters/`
(`JsonCodec`, evictions, observers, `KeyValueRecords`, `KeyEncoder`), `infrastructure/` (one
caller per key, the clock), `services/` (the operations: `Cache`, `Idempotency`, `QueryCaching`),
`entrypoint/` (`facades.py`: `CacheFacade`, `ConfiguredCache`, `IdempotencyFacade`, the global
`cache` / `idempotency`; `bus_caching.py`: `BusCachingMixin`) — the facades delegate to the
services, and every store operation is on `KeyValueStore` alone. Which store a bus uses
is the context's: `context/entrypoint/bus_store.py` (`bus.store()`) and
`context/entrypoint/resolution.py` (`store_context_in`, `current_store`, `key_value_store_of`) — the
same rule the circuit breakers and leases follow. `DatabaseIdempotencyRecords` lives in `orm/`.
Only `QueryCaching` knows repositories; `invalidate_on_commit` lives in `orm/` (`[sqlalchemy]`).
`redis` and `pymemcache` are optional extras, never core dependencies.

**Inside a consumer service** (one `UseFramework` per bounded context, created in
`infrastructure/framework.py` before `services/` is imported):

```
app.py / main.py              # store_context_in(RedisKeyValue(redis))   once, the whole service (else memory)
domains/billing/
  __init__.py                 # 1. billing = config_billing_framework("billing")
                              # 2. from . import services          (registers the handlers)
                              # 3. billing.cache.queries(QueryBalance)   (after handlers, before the first call)
  infrastructure/
    framework.py              # billing.store_context_in(KeyValueContexts(RedisKeyValue(own)))  only for a Redis of its own
    dependencies.py           # SiatClient(soap, cache=billing.cache)   an adapter is handed the facade;
                              # invalidate_on_commit(database, billing.cache.queries())
  services/
    query_balance.py          # a plain ApplicationService: no cache code
    issue_receipt.py          # @billing.feature(Cmd) + @billing.idempotency
    exchange_rate.py          # @billing.cache.ttl(timedelta(minutes=5)) on a function of the context
  adapters/
    siat_client.py            # self.products = cache.ttl(timedelta(hours=1)); self.products.get(key, compute, codec)
entrypoints/
  consumer.py                 # Subscriber(..., billing.cache.queries().invalidated_by({InvoiceIssued: [Invoice]}))
```

An adapter never imports the bus (layering, and an import cycle): it is handed `billing.cache` in
`dependencies.py`. `bus.cache.queries(Query)` needs the handler already registered (else
`TypeError`); called after the bus was built, it caches from the next execution. A `QueryCaching`,
`Cache` or `Idempotency` built by hand on a store of its own is still injected as a dependency.

**The flow of one call:**

```
bus(QueryBalance)  → interceptor from caching.on
  key = bus + Query + its values + response-schema hash + context keys in vary_by
  held, tag versions unchanged, fresh      → answered, use case not run
  missing / expiring → winner of store.add runs the use case, noting what it read → cached with those tags
repository.save(Invoice) + commit → invalidate_on_commit → increment tag "Invoice" → next read recomputes
another process: InvoiceIssued → Subscriber → invalidated_by bus → increment tag

bus(CommandIssueReceipt) → @billing.idempotency: key = bus name + Command + idempotency_key()
                                                 + whose it is (tenant/user of the context) + vary_by
  claim won (atomic add) → execute → answer stored for expires_after
  completed, same payload → answer replayed, execute not run
  other payload → KeyReused;  still running past wait_for_completion → AlreadyInProgress
  execute raised → claim released, the retry runs
```

## Mistakes an agent makes

- **A Query that reads through an adapter.** Only repository reads are noted; an answer that read
  nothing noted and declares no `depends_on` is answered and **never cached** (a log warning on every such call). Add
  `QueryCachePolicy(depends_on=[...])`, or cache the remote call with `Cache` in the adapter.
- **Writes that never invalidate.** A `MemoryRepository`, a raw session without
  `invalidate_on_commit`, another process without `invalidated_by`, or a second `QueryCaching` in
  another `namespace`: answers stay stale until their ttl.
- **A tenant that is not in the context.** `vary_by` names **context keys only**; the
  authenticated identity is not put in the context by default, so with nothing in the context
  every tenant shares one answer. Give the key from the identity with a context provider:
  `@billing.context_provider(gives=["tenant_id"])` returning `{"tenant_id": security.identity.tenant}`
  (`references/query-caching.md`).
- **A tenant or user carried in the context but missing from `vary_by`.** One tenant's answer is
  served to another. List those keys in `QueryCaching(sensitive=...)`: a call that carries one the
  policy does not vary by is refused (`ProgrammingError`), checked on every call, not at `on()`.
- **A tenant that is not in the context, again**: `bus.cache` and `bus.idempotency` key by the
  context's `tenant_id`, `tenant_ids` and `user_id` with nothing declared — only when the context
  carries them. Put them there with a context provider, not in `vary_by` by hand.
- **Importing the bus into an adapter** to reach `billing.cache`: hand it the facade in
  `dependencies.py` (`SiatClient(cache=billing.cache)`) and store with `configured.get(key, compute, codec)` (a `ConfiguredCache`).
- **A cached function with no return type on a shared store**: cached in this process only (said once
  in the log) — declare the return type, or pass a codec by hand.
- **`.exactly_once(records)` around a call to another service**: only what is written on the
  records' database rolls back with the answer; hand the other service the request's key.
- **A Command with no `idempotency_key()` and a generated field** (`Field(default_factory=uuid4)`,
  a timestamp): every retry is a new key and the write runs again.
- **No store set, or `InMemoryKeyValue`, on several replicas.** Each replica has its own answers,
  tag versions and claims: an invalidation or a claim on one is unseen on the others.
  `store_context_in(RedisKeyValue(redis))` once, or `bus.store_context_in(...)` for a Redis of its own.
- **`in_progress_for` shorter than the longest run.** The claim expires mid-run and a duplicate runs.
- **`get_or_compute(key, compute)` with no policy.** `CachePolicy()` defaults to
  `TimeToLive(ttl=None)`: the value is cached until forgotten or a validation rejects it.
- **`FailSafe(serve_for=...)` with default `errors=Exception`.** It serves the old value through
  a 401 or a bug, not only an outage. Name the transient errors.
- **A `Cache` key that omits a parameter** the value depends on (the tenant): one value is shared.

## QueryCaching — a Query's answer, invalidated by what it read

```python
from datetime import timedelta
from sincpro_framework.data_layer.caching import QueryCachePolicy, QueryCaching
from sincpro_framework.data_layer.store import InMemoryKeyValue

caching = QueryCaching(InMemoryKeyValue(), sensitive=("user_id",))
caching.on(billing, QueryBalance, QueryCachePolicy(ttl=timedelta(minutes=5), vary_by="tenant_id"))
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
from sincpro_framework.data_layer.caching import Idempotency
from sincpro_framework.data_layer.caching.domain import exceptions

idempotency = Idempotency(InMemoryKeyValue())          # RedisKeyValue(...) on replicas

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
from sincpro_framework.data_layer.caching import Cache, ExternalVersion, FailSafe, JsonCodec, CachePolicy, TimeToLive

tenants = Cache(InMemoryKeyValue(), namespace="catalog")     # Cache() holds objects in this process
policy = CachePolicy[Tenant](
    freshness=TimeToLive(ttl=timedelta(minutes=5), jitter=0.1, stale_for=timedelta(minutes=1)),
    validation=ExternalVersion(registry.current_version, version_of=version_of, trusted_for=timedelta(minutes=5)),
    failure=FailSafe(serve_for=timedelta(hours=1), errors=[ConnectionError, TimeoutError]),
)
value = tenants.get_or_compute(("catalog", "acme"), lambda: registry.resolve("acme"), policy, JsonCodec(Tenant))
```

- A store-backed `Cache` stores bytes: a `Codec` is required (refused without one).
- One caller recomputes; the others serve stale or wait. An exception is never cached.
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
