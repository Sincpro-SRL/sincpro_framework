---
name: sincpro-framework-caching
description: Cache and deduplicate in sincpro_framework — QueryCaching (a Query's answer kept and invalidated by what it read), Cache (any value judged by freshness/validation/fail-safe), Idempotency (a write runs once per key across replicas), and the KeyValueStore providers. Use whenever a task caches an answer, makes a command idempotent, adds a retry-safe write, or wires Redis/Valkey/Memcached.
---

# sincpro-framework-caching

A cache is decided where a bounded context is composed, like its repository or its queue. **The use
case is written exactly as without one.** Depth: `docs/caching/README.md`, PRD_13.

`KeyValueStore` is the specification (`get_many`, `set`, `add` atomic, `increment` atomic,
`delete`, `take` atomic, `get`). `InMemoryKeyValue` needs nothing; `RedisKeyValue`/`MemcachedKeyValue`
take the client, behind `[redis]`/`[memcached]`. A store of yours proves itself with
`KeyValueStoreContract`.

## The one rule

**Infrastructure the use case never sees.** Which Queries keep their answers, for how long and per
what, lives in the composition, not in `execute`. A cached or replayed answer still runs inside the
bus — its span, its access guard and its metrics — never `execute` directly.

## QueryCaching — a Query's answer, invalidated by what it read

```python
from sincpro_framework.caching import CachePolicy, InMemoryKeyValue, QueryCaching

caching = QueryCaching(InMemoryKeyValue(), sensitive=("user_id",))
caching.on(billing, QueryBalance, CachePolicy(ttl=timedelta(minutes=5), vary_by=("tenant_id",)))
```

- `on(bus, Query, CachePolicy(...))` registers an interceptor around the Query — decided **before**
  the bus is built, reaches it however it is executed. **Only Queries**: a Command decides from the
  database, never a cache.
- The repository notes every aggregate a read touched; those are the answer's **tags**. Writing an
  aggregate invalidates it (`caching.invalidate(Invoice)`, or `invalidate_on_commit(database,
  caching)` on SQL, after the commit). Writes from another process arrive as events:
  `caching.invalidated_by({InvoiceIssued: [Invoice]})` is a bus to hand the `Subscriber`.
- A context key named `sensitive` that the policy does not `vary_by` is refused — two users never
  share an answer. An answer that read nothing noted, and declares no `depends_on`, is answered and
  never kept.
- `namespace=` isolates contexts sharing one store; `enabled = False` rules the cache out without
  recomposing; `policies()` says what is cached.

## Idempotency — a write runs once per key

```python
from sincpro_framework.caching import AlreadyInProgress, Idempotency, KeyReused

idempotency = Idempotency(InMemoryKeyValue())          # RedisKeyValue(...) on replicas
receipts.ignore_sentry_exceptions(AlreadyInProgress, KeyReused)

@receipts.feature(CommandIssueReceipt)
@idempotency.once(
    expires_after=timedelta(minutes=2),
    in_progress_for=timedelta(minutes=1),
    wait_for_completion=timedelta(seconds=5),
    vary_by=("tenant_id",),
)
class IssueReceipt(Feature): ...
```

- `once(...)` wraps the use case's own `execute`; the use case still runs inside the bus. A
  subclass that keeps `execute` keeps `once()`.
- The Command says what identifies one request with `idempotency_key()` (or is keyed by every field).
  The other fields are still compared: same key, other payload ⇒ `KeyReused`; duplicate still in
  flight past the wait ⇒ `AlreadyInProgress`. Both are `DomainError`s — list them as expected.
- On a `KeyValueStore` it is best-effort for a replica that dies between the side effect and the
  record. For exactly-once within a database, implement `IdempotencyRecords` over your database so
  the record commits in the same transaction, and prove it with `IdempotencyRecordsContract`.
- `current_idempotency_key()` inside the write is the key to hand a downstream system.

## Cache — any value, judged by a policy

```python
from sincpro_framework.caching import Cache, ExternalVersion, JsonCodec, KeepPolicy, TimeToLive

tenants = Cache(InMemoryKeyValue(), namespace="catalog")
policy = KeepPolicy[Tenant](
    freshness=TimeToLive(ttl=timedelta(minutes=5), jitter=0.1, stale_for=timedelta(minutes=1)),
    validation=ExternalVersion(registry.current_version, kept=version_of, trusted_for=timedelta(minutes=5)),
    failure=FailSafe(serve_for=timedelta(hours=1), errors=(ConnectionError, TimeoutError)),
)
value = tenants.get_or_compute(("catalog", "acme"), lambda: registry.resolve("acme"), policy, JsonCodec(Tenant))
```

- **freshness**: `TimeToLive(ttl=, jitter=, stale_for=, early_expiry=)` (alias `Lifetime`) or
  `Sliding(idle_for=, at_most=)`.
- **validation**: `Unconditional()` (default) or `ExternalVersion(...)` (an ETag — a moved version
  forces recompute, never serves stale).
- **failure**: `Raise()` (default) or `FailSafe(serve_for=, throttle_for=, errors=)` (RFC 5861
  `stale-if-error` — serves the last good value through an outage, but never one validation
  rejected).
- One caller recomputes; the others serve stale or wait. An exception is never kept.
- Each call reports one outcome to the observer (`HIT`, `STALE`, `COMPUTED`, `COALESCED`,
  `INVALIDATED`, `FALLBACK`, `BYPASSED`) — by default a span event and `sincpro.cache.outcomes`.
- A shared store failure opens a breaker (`bypass_for`, 30 s): the call is answered on the process
  tier and reports `BYPASSED`. `forget` is the exception — an invalidation that reached no replica
  raises.

## References

- [references/query-caching.md](references/query-caching.md) — `QueryCaching` in full
- [references/cache-and-idempotency.md](references/cache-and-idempotency.md) — `Cache` policies and `Idempotency`
- [references/providers.md](references/providers.md) — `KeyValueStore`, providers, contracts, `KeyValueRuns` (crons)

## Related

- Reading a query once into DataFrames (in-process, analysis): `sincpro-framework-criteria`
- Crons across replicas share `KeyValueRuns`: `sincpro-framework-operations`
- `@idempotency.once` in a queue/entrypoint: `sincpro-framework-entrypoints`
