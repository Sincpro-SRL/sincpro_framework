# `Cache` and `Idempotency`

Deeper, in the framework repo: `docs/caching/README.md` §"Any value" and §"A write that runs once".

## `Cache` — any value by its parameters

`QueryCaching` knows when to let go because the repository saw what an answer read. What another
system answers — a tenant a token resolves to, a catalog a service publishes — gives no such signal;
it gives a **version**. `Cache` keeps a value by its parameters and judges it by strategies.

```python
from datetime import timedelta

from sincpro_framework.caching import (
    Cache, ExternalVersion, FailSafe, InMemoryKeyValue, JsonCodec, KeepPolicy, TimeToLive,
)

tenants = Cache(InMemoryKeyValue(), namespace="catalog")
policy = KeepPolicy[Tenant](
    freshness=TimeToLive(ttl=timedelta(minutes=5), jitter=0.1, stale_for=timedelta(minutes=1)),
    validation=ExternalVersion(registry.current_version, kept=version_of, trusted_for=timedelta(minutes=5)),
    failure=FailSafe(serve_for=timedelta(hours=1), errors=(ConnectionError, TimeoutError)),
)
value = tenants.get_or_compute(("catalog", "acme"), lambda: registry.resolve("acme"), policy, JsonCodec(Tenant))
tenants.forget(("catalog", "acme"), JsonCodec(Tenant))
```

- `Cache()` with no store keeps the objects themselves in this process (no codec, nothing leaves
  the process — right for a value carrying a credential). `Cache(store)` keeps bytes shared by
  replicas and requires a `Codec` on every call.
- The key is the parameters, written canonically and hashed — a token used as a key is never readable
  in the store. Every parameter the value depends on (the tenant) belongs in the key.
- `KeepPolicy()` (also what a call with no policy gets) is `TimeToLive(ttl=None)`: kept until
  forgotten or until a validation rejects it. Give a shared-store value a ttl.
- **freshness** — `TimeToLive(ttl=, jitter=, stale_for=, early_expiry=)` (alias `Lifetime`) or
  `Sliding(idle_for=, at_most=)`. On a shared store a sliding hit renews at most once per quarter of
  the window.
- **validation** — `Unconditional()` (default, freshness is the whole answer) or
  `ExternalVersion(current, kept=, trusted_for=)` (an ETag; a moved version forces recompute, never
  serves stale).
- **failure** — `Raise()` (default) or `FailSafe(serve_for=, throttle_for=30s, errors=(Exception,))`
  (serves the last good value through an outage, re-keeps it fresh for `throttle_for`; never serves a
  value validation *rejected*). Name `errors` (`ConnectionError`, `TimeoutError`): the default
  covers every exception, a 401 included.
- On a shared store, values are bytes, so the call names its `Codec` (e.g. `JsonCodec(Tenant)`).
- An exception is never kept; a value larger than `max_bytes` is answered and not kept.
- The process tier is bounded by an `Eviction` (`Lru(max_entries=10_000)` default, `Unbounded()`).

### Outcomes and the breaker

Each call reports one outcome to the cache's observer: `HIT`, `STALE`, `COMPUTED`, `COALESCED`,
`INVALIDATED`, `FALLBACK` or `BYPASSED`. By default each is a span event and one more on
`sincpro.cache.outcomes` (`MetricsObserver`); `CountingObserver` counts them. An observer that
raises never breaks the call.

A shared-store exception opens its breaker for `bypass_for` (30 s): the call is answered on the
process tier (still one caller per key) and reports `BYPASSED`; a failure to *write* never fails the
call. `forget` is the exception: an invalidation that reached no replica raises.

## `Idempotency` — a write that runs once

```python
from sincpro_framework import DataTransferObject
from sincpro_framework.caching import AlreadyInProgress, Idempotency, InMemoryKeyValue, KeyReused

class CommandIssueReceipt(DataTransferObject):
    request_id: str
    total: int
    def idempotency_key(self) -> str:          # what identifies one request
        return self.request_id

idempotency = Idempotency(InMemoryKeyValue())  # RedisKeyValue(...) on replicas
receipts.ignore_sentry_exceptions(AlreadyInProgress, KeyReused)

@receipts.feature(CommandIssueReceipt)
@idempotency.once(
    expires_after=timedelta(minutes=2),        # a transport retry, not a person
    in_progress_for=timedelta(minutes=1),      # a claim left by a dead replica expires
    wait_for_completion=timedelta(seconds=5),  # a duplicate mid-run waits for the answer
    vary_by=("tenant_id",),
)
class IssueReceipt(Feature): ...
```

- Claim, run, remember: exactly one caller runs; a completed answer is replayed; a failure releases
  the claim so the retry runs.
- The fields not in the key are still compared — same key, other payload ⇒ `KeyReused` (the IETF
  `Idempotency-Key` 422); a duplicate still in flight past `wait_for_completion` ⇒
  `AlreadyInProgress` (409). Both are `DomainError`s — expected traffic.
- The key: the Command's `idempotency_key()`; else the key a transport put in the context under
  `IDEMPOTENCY_KEY` (REST's `Idempotency-Key` header); else every field of the Command — so a field
  generated per call (a `default_factory` id, a timestamp) makes every retry a new request.
- `vary_by` names the context keys the key is kept per. Without the tenant, two tenants sending
  the same `request_id` share one answer.
- `in_progress_for` must outlive the longest run: an expired claim lets a duplicate run.
- `once(...)` wraps the use case's own `execute`; the use case still runs inside the bus (Sentry,
  span, access guard), so a replayed answer is still authorized. A subclass that keeps `execute`
  keeps `once()`; a `replaces=` handler declares its own. An `async def execute` is refused; async
  callers reach it through `bus.get_async_bus()`. `execute` must declare its return type (the
  replayed answer is decoded as it).
- By hand, around anything: `idempotency.run(key, write, IdempotencyPolicy(expires_after=...),
  JsonCodec(Answer), payload=body)`.
- Underneath it needs a place replicas share to claim a key and keep its answer:
  `KeyValueRecords(store)` (best effort for a replica that dies after the side effect), or your
  `IdempotencyRecords` over a database so the record commits in the write's transaction
  (`IdempotencyRecordsContract`). Inside the write, `current_idempotency_key()` is the key.
