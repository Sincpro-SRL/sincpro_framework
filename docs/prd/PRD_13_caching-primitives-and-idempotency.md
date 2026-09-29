# PRD_13: Caching and idempotency — the specification

- **Status**: specification complete. Phase 1 built (`Cache`, `KeepPolicy`, `Lifetime`,
  `Unconditional`/`ExternalVersion`, `Idempotency` with `once()`, `IdempotencyRecords`,
  `KeyValueStore.take`, observers), documented in [docs/caching](../caching/README.md). Phases 2
  and 3 specified here, to build before and after the release. Every section says what is built.
- **Depends on**: `KeyValueStore` and its providers, interceptors (`bus.interceptor`, for
  `QueryCaching`), the request context (`Feature.context`), `DomainError`.
- **Shapes**: policies are frozen DTOs; strategies and ports are abstract classes or `Protocol`s;
  the records a store keeps are dataclasses mapped field by field; what a Command offers is a
  `Protocol` (`IdempotentCommand`).
- **Philosophy**: the contract is what lasts. The framework defines what a kept value is, when it
  stops being served, what "run once" means across replicas, and implements the orchestration once
  for every store — the store stays dumb on purpose, as in every mature library. A built-in ships
  for each port and strategy; a project uses it or plugs its own, proven by a contract suite.

## Problem

Until now the only cache was `QueryCaching`: a Query's answer, let go of when an aggregate it read
is written. That is the right tool when the data lives in this process's repositories, and the
wrong one everywhere else. `sincpro_mcp_odoo` caches what lives in *other* systems — the tenant a
token resolves to, the schema a customer's Odoo publishes — whose only signal of change is a
version the source publishes. `QueryCaching` refuses those answers ("read nothing … not kept"), so
the project wrote its own `VersionedCache`, its own key-value port (`SessionStore`) and its own
`IdempotencyCache` — and the idempotency one has the race every hand-written one has: *check,
run, remember* is three steps, and a client retry that lands on another replica while the first
call is still running writes twice.

The algorithms that would have prevented it — single-flight on the store's atomic `add`, jittered
lifetimes, XFetch early recomputation — already existed, as private methods of `QueryCaching`,
reachable only through a Query and the ORM.

## Background — what mature libraries settled on

| Library | Its shape | What this spec takes |
|---|---|---|
| **dogpile.cache** | `region.get_or_create(key, creator, expiration_time, should_cache_fn)`; the dogpile lock | `Cache.get_or_compute(key, compute, policy)`; one caller recomputes; `CachePredicate` |
| **Symfony Cache Contracts** | `get($key, $callback, $beta)` — `beta` is XFetch's β; tag-aware pools; marshaller chain | `early_expiry=β`; tags; codec wrappers |
| **.NET HybridCache** | `GetOrCreateAsync(key, factory, options, tags)`; L1 + L2; 1 MB payload cap; coalescing per instance only | the same contract; `max_bytes`; cross-replica coalescing it lacks |
| **FusionCache (.NET)** | fail-safe (serve the last good value, throttled), soft/hard factory timeouts, eager refresh, L2 circuit breaker, backplane of notifications | `FailurePolicy`, the store breaker, the `Backplane` port |
| **Caffeine** | `refreshAfterWrite` vs `expireAfterWrite`; W-TinyLFU; size/weight bound; a failed refresh keeps the old value | `trusted_for` vs `ttl`; `Eviction` |
| **HTTP caching (RFC 9111, RFC 5861)** | freshness lifetime; `stale-while-revalidate`, `stale-if-error`; validators (`ETag`) | `stale_for`; `ExternalVersion` is an ETag; `FailSafe` is stale-if-error |
| **Django / Rails** | `add`, `incr`, key `version=`, `KEY_FUNCTION`; recyclable versioned keys, `race_condition_ttl` | canonical hashed keys; the codec's schema in the key |
| **Spring Cache / NestJS / ASP.NET OutputCache / Powertools** | declaration on the handler (`@Cacheable`, `[OutputCache(Policy)]`, `@idempotent`) | `@idempotency.once(...)`, `@caching.keeps(...)` |
| **IETF `Idempotency-Key` draft (expired, guidance only); Stripe** | same key + same payload → replay; other payload → 422; in flight → 409 | `KeyReused`, `AlreadyInProgress`, replay of the completed answer |
| **AWS Powertools — Idempotency** | `INPROGRESS`/`COMPLETED`, `expires_after`, in-progress expiry, payload validation, release on exception | `IdempotencyPolicy`, the fingerprint |
| **Brandur, "Stripe-like idempotency keys in Postgres"; AWS Builders' Library** | the record commits in the same transaction as the write; foreign calls carry a derived key | `IdempotencyRecords`; `current_idempotency_key()` |

## The specification

The words **MUST**, **MUST NOT**, **SHOULD** and **MAY** are used as in RFC 2119. "The cache" is
`Cache` and `QueryCaching` alike unless a section names one.

### 1. Terms

| Term | Meaning |
|---|---|
| **key** | the parameters a value is kept under, written canonically and hashed (§2) |
| **kept value** | a value, its **mark** and its **timeline**, in one tier |
| **mark** | the version a value was computed at, as its `Validation` settles it — `""` when it has none |
| **timeline** | `born` · `fresh_until` · `stale_until` · `last_resort_until` · `checked_until` (§4) |
| **tier** | where kept values live: the **process** tier (objects) or the **shared** tier (bytes in a store) |
| **leader** | the one caller computing a key right now (§5) |
| **outcome** | what a call did, reported to the observer (§9) |
| **claim** / **record** | idempotency: the atomic reservation of a key, and what is kept under it (§10) |

### 2. The key

1. A key **MUST** be the parameters written canonically — a decimal by its value, a date in ISO, a
   mapping by sorted keys, a set sorted, a model by its dump — then hashed with SHA-256 and kept
   as 32 hex characters, under `cache:<namespace>:value:` or `idempotency:<namespace>:`.
2. A key **MUST NOT** carry a parameter in clear: a token used as a key is not readable in
   `KEYS *`.
3. On the shared tier the key **MUST** include the codec's `schema`: a deploy that changes a
   value's shape reads a key of its own.
4. A value scoped to a tenant, a user or any identity **MUST** have that scope among its
   parameters — the cache cannot infer it. `QueryCaching` refuses a Query whose context carries a
   `sensitive` key its policy does not vary by; `Cache` and `once()` take the scope in their key
   parameters or `vary_by`.

*Built.*

### 3. Tiers and what each guarantees

| | Process tier | Shared tier | Bypassed (§8) |
|---|---|---|---|
| Holds | the objects themselves — nothing encoded | bytes, through a `Codec` | the process tier, standing in |
| Visible to | this process | every replica on the store | this process |
| Survives a restart | no | yes, until the store expires it | no |
| Bounded by | `Eviction` (default `Lru(10_000)`) | the store's TTL, `max_bytes` per value | as the process tier |
| `forget(key)` reaches | this process | every replica | this process — the store is not asked |
| Coalescing | one leader per process | one leader across replicas | one leader per process |
| Consistency | read-your-writes | last writer wins; a lost leader lock costs one duplicate computation, never a wrong value | as the process tier; nothing written during a bypass reaches the store |

- A value carrying a credential (a tenant's API key, a password) **MUST** be kept on the process
  tier: nothing of it leaves the process.
- The shared tier **MUST** tell the store to keep a value until the end of its timeline
  (`last_resort_until`, or `stale_until` without a `FailurePolicy`), so a value is never gone
  while it may still be served.

*Built: both tiers, the shared tier's TTL and `max_bytes`. Phase 2: `Eviction`, the bypass.*

### 4. The timeline of a kept value

```text
born ──── fresh ────► fresh_until ──── stale ────► stale_until ── last resort ──► last_resort_until
          HIT                         STALE while a leader                FALLBACK only when computing
          (validated per §6)          recomputes                          or validating fails
```

- `fresh_until`, `stale_until` come from `Freshness` (§7.1); `last_resort_until` from
  `FailurePolicy` (§7.3); `checked_until` is `born` (or the last check that held) plus the
  validation's `trusted_for`.
- `stale_until` **MUST** be ≥ `fresh_until`, and `last_resort_until` ≥ `stale_until`.

*Built: fresh and stale, `checked_until`. Phase 2: `born`, `last_resort_until`.*

### 5. Coalescing

1. At most one caller per key and tier **MUST** compute at a time: on the shared tier, the winner
   of the store's `add` on `<key>:lock`, held for `5 × wait_for_others`; on the process tier, a
   lock per key.
2. A caller that does not lead **MUST** serve the kept value when it is servable and valid, or
   wait up to `wait_for_others` for the leader's value and then compute itself — it **MUST NOT**
   hang, and **MUST NOT** serve the invalid value it read before waiting.
3. A leader **MUST** release its lock whatever `compute` does.

*Built.*

### 6. How a call is answered

The order is normative. Each row names the outcome it reports (§9).

| # | Situation | Answer | Kept | Outcome |
|---|---|---|---|---|
| 0 | `enabled = False` | `compute()` | nothing | — |
| 1 | shared tier, breaker open, or the store fails on read or lock | answered on the process tier from row 2 | on the process tier | `BYPASSED`, then the row's |
| 2 | nothing kept, or what is kept cannot be decoded | row 6 | | |
| 3 | kept and servable (`now < stale_until`); check still trusted (`now < checked_until`) | row 4 | | |
| 3a | …check not trusted: `validation.holds(mark)` answers `True` | row 4; `checked_until` renewed | | |
| 3b | …answers `False` | row 6, with **no** fallback allowed | | `INVALIDATED` |
| 3c | …raises | the `FailurePolicy`: fall back (row 7) or raise | | |
| 4 | valid and fresh, not picked by XFetch | the kept value | | `HIT` |
| 5 | valid but expiring — leads | recompute (row 6's leader) | | |
| 5a | valid but expiring — does not lead | the kept value | | `STALE` |
| 6 | missing or invalid — leads | `compute()`; kept if `CachePredicate` keeps it | yes, if kept | `COMPUTED` |
| 6a | missing or invalid — does not lead | the leader's value, or `compute()` after `wait_for_others` | | `COALESCED` / `COMPUTED` |
| 7 | `compute()` raised, a value is kept that was not rejected (3b) and `now < last_resort_until` | the kept value, re-kept fresh for `throttle_for` | yes | `FALLBACK` |
| 7a | …no such value, or `Raise` | the exception | nothing | — |

- A failure to **write** to the store **MUST NOT** fail the call: the value is answered, the
  breaker is tripped (§8).
- An exception **MUST NOT** be kept.

*Built: rows 0, 2–6a, 7a. Phase 2: rows 1, 7, the write rule; `CachePredicate` phase 3.*

### 7. Strategies — the contract each one owes

Every strategy **MUST** be pure unless its contract says otherwise: no I/O, the same answer for the
same arguments. A call picks its strategies in its `KeepPolicy`.

#### 7.1 `Freshness` — how long a value is served as is

```python
class Freshness(ABC):
    def fresh_until(self, now: float, roll: float) -> float: ...           # NEVER for no expiry
    def stale_until(self, fresh_until: float) -> float: ...                # ≥ fresh_until
    def recompute_early(self, fresh_until: float, took: float, now: float, roll: float) -> bool: ...
    def on_hit(self, born: float, fresh_until: float, now: float, roll: float) -> float | None: ...
```

`roll` is a uniform draw in [0, 1) the cache supplies, so a test fixes it. `on_hit` answers a new
`fresh_until`, or `None` to leave it — what a sliding expiration renews.

| Built-in | Behaviour | Status |
|---|---|---|
| `TimeToLive(ttl, jitter, stale_for, early_expiry)` | `ttl` ± `jitter`; `stale_for` of stale-while-revalidate; XFetch with β = `early_expiry` (Vattani et al., VLDB 2015); `ttl=None` never expires | built as `Lifetime` |
| `Sliding(idle_for, at_most)` | fresh for `idle_for` after the last hit, never past `born + at_most` | phase 2 |

#### 7.2 `Validation` — whether a kept value is still right

```python
class Validation[T](ABC):
    trusted_for: timedelta                            # a check that held is believed this long
    def before(self) -> str | None: ...               # the mark taken before computing
    def after(self, value: T, before: str | None) -> str | None: ...   # None: answer, keep nothing
    def holds(self, mark: str) -> bool: ...
```

- `holds` **MAY** do I/O — it is what every check costs, so it **SHOULD** be the cheapest call the
  source has.
- `holds` answering `False` means *wrong*: that value **MUST NOT** be served again, fallback
  included. Raising means *cannot tell*: the `FailurePolicy` decides.

| Built-in | Behaviour | Status |
|---|---|---|
| `Unconditional()` | always holds — the freshness is the whole answer | built |
| `ExternalVersion(current, kept=, trusted_for=)` | the version the source publishes (an ETag); `kept` reads it off the value instead of asking before computing | built |
| `TagVersions(tags)` | generation counters in the store: a tag bumped means every value that depended on it is wrong | built inside `QueryCaching`; exposed for `Cache` phase 3 |

#### 7.3 `FailurePolicy` — what a failing computation answers

```python
class FailurePolicy(ABC):
    last_resort_for: timedelta      # how long past stale_until a value may still be served
    throttle_for: timedelta         # how long a fallback is re-kept fresh, sparing the source
    def handles(self, error: Exception) -> bool: ...   # BaseException is never handled
```

| Built-in | Behaviour | Status |
|---|---|---|
| `Raise()` | the default: nothing is served past `stale_until` | phase 2 (today's behaviour) |
| `FailSafe(serve_for, throttle_for=30s, errors=(Exception,))` | row 7 of §6 | phase 2 |

A fallback **MUST NOT** serve a value its validation rejected (3b): fail-safe covers a source that
cannot answer, not one that answered no — serving it would hand out what the source revoked.

#### 7.4 `Eviction` — how the process tier stays bounded

```python
class Eviction(ABC):
    def touched(self, key: str) -> None: ...
    def admitted(self, key: str) -> list[str]: ...   # the keys to evict now that key is kept
    def forgotten(self, key: str) -> None: ...
```

It **MUST** be thread-safe, and after `admitted` the tier **MUST** hold no more than its bound.

| Built-in | Behaviour | Status |
|---|---|---|
| `Lru(max_entries=10_000)` | the default; least recently touched first | phase 2 |
| `Unbounded()` | today's behaviour; for a tier whose keys are known and few | phase 2 |

A project needing admission (W-TinyLFU) wraps `cachetools` or its own behind this port.

#### 7.5 `CachePredicate` — whether an answer is kept at all

```python
class CachePredicate[T](ABC):
    def keeps(self, value: T) -> bool: ...
    def freshness_for(self, value: T) -> Freshness | None: ...   # e.g. a not-found kept shorter
```

Built-ins `CacheAll()` (default), `SkipNone()`, `Negative(freshness)`. *Phase 3.*

### 8. Ports — the contract each one owes

| Port | Operations | It **MUST** | Proven by | Status |
|---|---|---|---|---|
| `KeyValueStore` | `get_many` · `set(ttl)` · `add(ttl)` · `increment` · `delete` · `take` · `get` | make `add`, `increment` and `take` atomic across every caller of the store; honour a ttl at its precision (Memcached: whole seconds) | `KeyValueStoreContract` | built |
| `Codec` | `encode` · `decode` · `schema` | round-trip what it encodes; raise on bytes of another shape; change `schema` when the shape changes | `CodecContract` | built; the contract phase 2 |
| `CacheObserver` | `observed(outcome, namespace)` | never block the call; the cache guards it — an observer that raises is ignored | — | built |
| `IdempotencyRecords` | `claim(hold)` · `read` · `complete(keep_for)` · `release(owner)` | make `claim` atomic; release only the owner's claim | `IdempotencyRecordsContract` | built |
| `Backplane` | `publish(notice)` · `subscribe(on_notice)` | carry notices (`key` or `tag`, `action`, `at`) and never values; tolerate lost notices — the L1 ttl is the safety net | `BackplaneContract` | phase 3 |

**The store breaker** (the shared tier's resilience, not a port): any exception from a store
operation opens it for `bypass_for` (default 30 s); while open the tier is bypassed (§6 row 1);
the first call after it tries the store again. Operation timeouts are the client's — an adapter
**SHOULD** document the socket timeout it expects its client to have. *Phase 2.*

### 9. Observability

Every call **MUST** report exactly one final outcome (plus `BYPASSED` when it applies) with the
cache's namespace:

- **Cache:** `HIT` · `STALE` · `COMPUTED` · `COALESCED` · `INVALIDATED` · `FALLBACK` · `BYPASSED`
- **Idempotency:** `CLAIMED` · `REPLAYED` · `IN_PROGRESS` · `KEY_REUSED`

The default observer adds each one as an event on the active span; `CountingObserver` counts them
for a metrics exporter. Without `FALLBACK` and `BYPASSED` counted, fail-safe hides an outage.
*Built for idempotency; for `Cache` phase 2.*

### 10. Idempotency

1. **The key** is the use case's class, the Command's class, the Command's `idempotency_key()`
   (every field when it has none) and the `vary_by` context keys. **The payload** `KeyReused`
   compares is the whole Command and those context keys.
2. **States**: nothing → `IN_PROGRESS` (claim, for `in_progress_for`) → `COMPLETED` (the answer,
   for `expires_after`); `IN_PROGRESS` → nothing when the run raises, or when the claim expires.
3. **Answers**:

   | The key holds | Same payload | Other payload |
   |---|---|---|
   | nothing | claim, run, complete — `CLAIMED` | same |
   | `COMPLETED` | the kept answer, not run — `REPLAYED` | `KeyReused` (422) |
   | `IN_PROGRESS` | wait up to `wait_for_completion`, then `AlreadyInProgress` (409) | `KeyReused` |

4. **The run** knows its key (`current_idempotency_key()`) to hand on to a system that
   deduplicates by key.
5. **Guarantees by records**: on `KeyValueRecords`, "once" holds for every retry except a replica
   that dies between its side effect and `complete` — its claim expires and the write re-runs. On
   records that complete in the write's own transaction, "once" holds for everything that
   transaction writes. **Neither** covers a side effect committed in *another* system whose answer
   this process never received: that run raised, released its claim, and the retry runs again —
   hand the key on, or look the effect up before writing again.
6. **The project MUST**: keep `in_progress_for` above the longest run; list `AlreadyInProgress`
   and `KeyReused` in its `ignore_sentry_exceptions(...)`; give a replacement handler with its own
   `execute` its own `once()`.

*Built. Async use cases phase 2; replaying declared domain errors, a reconciler for expired
claims and an `Idempotency-Key` header in REST/gRPC phase 3.*

### 11. Defaults

| Setting | Default | Why |
|---|---|---|
| `Freshness` | `TimeToLive(ttl=None)` | a value with a validation needs no ttl; one on a shared store **SHOULD** set one |
| `Validation` | `Unconditional()` | |
| `FailurePolicy` | `Raise()` | serving stale data is a choice, never a surprise |
| `Eviction` | `Lru(10_000)` | an unbounded process tier is a memory leak |
| `wait_for_others` | 2 s | a waiter never hangs |
| `max_bytes` | 1 MiB | HybridCache's cap |
| `bypass_for` | 30 s | |
| observer | `SpanObserver` | outcomes land where the call is traced |
| `in_progress_for` / `expires_after` | 1 min / — (required) | a transport retry, not a person on purpose |

### 12. Conformance

A store, records, a codec or a strategy of a project's conforms when it passes its contract suite
from `sincpro_framework.testing`: `KeyValueStoreContract` and `IdempotencyRecordsContract`
(built); `CodecContract`, `FreshnessContract`, `EvictionContract` (phase 2); `BackplaneContract`
(phase 3). Every built-in passes the same suite a project's does.

### 13. Maturity — capability by capability

| Capability | Where it comes from | Status |
|---|---|---|
| `get_or_compute(key, factory, options)` over a byte store | dogpile, Symfony, HybridCache, FusionCache | built |
| Coalescing in the process and across replicas | Go singleflight; FusionCache's distributed locker | built |
| TTL with jitter, stale-while-revalidate, XFetch | RFC 5861; Laravel `flexible`; Symfony `beta` | built |
| Validation against a version the source publishes | HTTP `ETag`; FusionCache conditional refresh | built |
| Hashed keys, schema in the key, decode error as a miss | HybridCache key guidance; Rails recyclable keys; dogpile | built |
| Tag invalidation by generation counters | Django `VERSION`, HybridCache logical tags | built for ORM reads; general phase 3 |
| Fail-safe, throttled | FusionCache; cashews `failover`; RFC 5861 `stale-if-error` | phase 2 |
| A failing shared store bypassed, not waited on | FusionCache circuit breaker; Laravel `failover` | phase 2 |
| A bounded process tier | Caffeine; MemoryCache `SizeLimit` | phase 2 |
| Sliding expiration | JCache `AccessedExpiryPolicy`; Caffeine `expireAfterAccess` | phase 2 |
| Every outcome observed | JCache statistics; Caffeine `recordStats` | idempotency built; `Cache` phase 2 |
| L1 over a shared L2, invalidated across replicas | FusionCache backplane (HybridCache lacks it) | phase 3 |
| Soft/hard factory timeouts, eager background refresh | FusionCache; Caffeine `refreshAfterWrite` | phase 3 |
| Negative caching, cache-if | dogpile `should_cache_fn`; Spring `unless`; Rails `skip_nil` | phase 3 |
| Compression / encryption of values | Symfony marshallers; Rails `compress_threshold` | phase 3 |
| Idempotency: claim first, fingerprint, in-progress expiry, release on error | Stripe; AWS Powertools | built |
| Records in the write's own transaction | Brandur; AWS Builders' Library | the port built |
| A key handed on downstream | Brandur | built |

**Not planned**, and why: write-behind and a cache writer (the ORM is the source of truth, writes
belong to Features); W-TinyLFU admission (`Lru` with a bound covers it; admission plugs in behind
`Eviction`); peer ownership à la groupcache; entry listeners (the observer covers the need); glob
deletes (tags do, without scanning a production store).

## Decisions

### How a Command says what identifies one request: a method

```python
class CommandIssueInvoice(DataTransferObject):
    request_id: str
    total: int

    def idempotency_key(self) -> str:
        return self.request_id
```

The key belongs to the Command, not to the use case that runs it (MediatR's convention: the
metadata lives on the request type). Weighed against:

| Option | Why not |
|---|---|
| `once(key_fields=("request_id",))` | a field named in a string: a rename breaks it silently |
| `request_id: Annotated[str, IdempotencyKey]` | works and is typed, but one more idiom to learn for a single feature |
| `once(key=CommandIssueInvoice.request_id)` — a field reference on the class | evaluated, prototyped, **deferred** (below) |

#### Deferred: `Command.field` as a field reference

A prototype made `CommandIssueInvoice.request_id` answer a `FieldReference(owner, name)` on the
class, the way ODMs on pydantic (Beanie, ODMantic) expose theirs, through a metaclass on
`DataTransferObject`. It worked — instances, validation, JSON, pickle, defaults untouched, the
full suite green — and was reverted, because of what it costs every DTO of every project for one
feature:

- it subclasses pydantic's **private** `pydantic._internal._model_construction.ModelMetaclass`;
- `hasattr(DTO, "field")` turns `True` — pydantic itself then warned "shadows an attribute" on
  every redeclared field until references were switched off while any model is built, and any
  other library probing a DTO class with `hasattr` could change behaviour unseen;
- a type checker reads `DTO.field` as the field's own type, so a parameter taking a reference must
  be typed `object`;
- without the "class complete" guard, pydantic recursed forever — two guards already, the sign
  of magic.

**Revisit when** field references earn their place beyond idempotency — typed `Criteria`
(`Invoice.customer_id == "c1"`), `vary_by` without context-key strings, projections — as a PRD of
their own that owns the metaclass, pins the pydantic versions it supports, and ships a type-checker
story (a stub or plugin) instead of `object`.

### A decorator on the use case, nothing wired on the bus

`@idempotency.once(...)` replaces the use case's `execute` with one that runs the original once
per key — AWS Powertools' `@idempotent`. Weighed against `AccessControl`'s shape (the decorator
only marks, `idempotency.on(bus)` wires a guard that reads the mark):

| | Wraps `execute` (chosen) | Mark + `on(bus)` guard |
|---|---|---|
| What a project writes | the decorator | the decorator **and** `on(bus)` per bus |
| Forgetting a step | impossible — there is one | a mark nobody reads: runs twice, silently |
| Authorization of a replay | kept: `execute` runs inside the access guard | kept: the guard sits inside it |
| `replaces=` | inherited only by a subclass that keeps `execute` | inherited by any replacement |
| Duplicates out of Sentry | the context lists them, like its other expected errors | `on()` could list them itself |
| The class | its `execute` is replaced | never written onto |

One step to write and none to forget outweighs `replaces=` inheritance. The same shape applies to
`QueryCaching`: `@caching.keeps(...)` on the Query's handler replaces `on(bus, Query, policy)` in
phase 2.

### Idempotency needs a record, not a cache

What "once" needs is a place replicas share to claim a key and keep its answer — a record with
states, not a value with a lifetime. That is why it has its own port (`IdempotencyRecords`) over
the same stores, and why a project can close the crash gap by completing the record in its own
transaction (§10.5).

### Policies per call, not named policies

ASP.NET names policies at startup and references them by name. Here a policy is a frozen DTO
built where it is used — a module constant when shared — because a name is a string that a rename
breaks, and a constant gives the same reuse with a reference the type checker follows.

## Phases

1. **Built** — everything marked built above.
2. **Before the release** — `Freshness` as a port (`TimeToLive`, `Sliding`); `FailurePolicy`
   (`Raise`, `FailSafe`); `Eviction` (`Lru` default, `Unbounded`); the store breaker and the bypass;
   `Cache` reporting every outcome; `@caching.keeps(...)`; `once()` on async use cases;
   `CodecContract`, `FreshnessContract`, `EvictionContract`.
3. **After** — `Backplane` and L1 over L2; soft/hard factory timeouts and eager refresh;
   `CachePredicate`; `TagVersions` for `Cache`; codec wrappers; the idempotency extras.

## Open questions

1. **Fail-safe on validation errors by default?** `FailSafe` covers a raising `holds` (row 3c).
   Should it be opt-out per error kind — a version endpoint timing out is not the same as it
   answering 401?
2. **`Sliding` on the shared tier.** Renewing `fresh_until` on every hit is a write per read;
   should it be allowed only on the process tier, or renew at most once per `idle_for / 4`?
3. **Replaying domain errors.** Stripe replays a failure once execution began; Powertools releases
   the key. Should `once()` replay the `DomainError`s a policy lists, and release on the rest?
4. **Async.** `once()` and `get_or_compute` on the async bus need async store operations — a
   second port (`AsyncKeyValueStore`) or async methods on the same one?
