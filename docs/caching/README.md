# Caching — infrastructure the use case never sees

A cache is decided where a bounded context is composed, like its repository or its queue. A
Feature or an ApplicationService is written exactly as without one; whoever wires the context
says which Queries keep their answers, for how long, per what, and on which store.

- **`KeyValueStore`** is the specification: the operations every provider has. The core ships
  `InMemoryKeyValue` (standard library only); `RedisKeyValue` (Redis and Valkey) and
  `MemcachedKeyValue` take the client you build, behind the `[redis]` and `[memcached]` extras.
  A store of yours proves itself with `sincpro_framework.runtime.testing.KeyValueStoreContract`.
- **`QueryCaching`** keeps a Query's answer and lets go of it when an aggregate it read is
  written — the repository notes the reads, nobody declares them.
- **`Cache`** keeps any value by its parameters — what another system answered, a computation —
  judged by the strategies of each call: a `Freshness` (how long), a `Validation` (whether it is
  still right, such as the version the source publishes) and a `FailurePolicy` (what a failing
  source answers). No ORM, no Query.
- **`Idempotency`** runs a write once per key across replicas, and replays its answer to a retry.
- **`KeyValueRuns`** is the record several cron replicas share: one of them runs each tick.

Every block on this page runs, in order, in `tests/docs/test_persistence_guide.py`.

## A use case that knows nothing

```python
from dataclasses import dataclass
from datetime import timedelta

from sincpro_framework import ApplicationService, DataTransferObject, UseFramework
from sincpro_framework.ddd import Criteria, Entity
from sincpro_framework.data_layer.repositories import MemoryRepository


@dataclass
class Customer(Entity):
    name: str = ""


@dataclass
class Invoice(Entity):
    customer_id: str = ""
    total: int = 0


class QueryBalance(DataTransferObject):
    customer_id: str


class ResponseBalance(DataTransferObject):
    customer: str
    balance: int


repository = MemoryRepository()
repository.save(Customer(id="c1", name="Ana"))
repository.save(Invoice(id="i1", customer_id="c1", total=100))
billing = UseFramework("billing", log_after_execution=False)
billing.add_dependency("repository", repository)
computed: list[str] = []


@billing.app_service(QueryBalance)
class Balance(ApplicationService):
    def execute(self, dto: QueryBalance) -> ResponseBalance:
        computed.append(dto.customer_id)
        customer = self.repository.get(Customer, dto.customer_id)
        invoices = self.repository.fetch_all(
            Invoice,
            Criteria.model_validate({"where": {"field": "customer_id", "operator": "=", "value": dto.customer_id}}),
        )
        return ResponseBalance(customer=customer.name, balance=sum(one.total for one in invoices))
```

## Where the context is composed: which Queries keep their answers

```python
from sincpro_framework.data_layer.caching import CachePolicy, QueryCaching
from sincpro_framework.common.store import InMemoryKeyValue

caching = QueryCaching(InMemoryKeyValue(), sensitive=("user_id",))
caching.on(billing, QueryBalance, CachePolicy(ttl=timedelta(minutes=5), vary_by="tenant_id"))

with billing.context({"tenant_id": "acme"}):
    first = billing(QueryBalance(customer_id="c1"), ResponseBalance)
    again = billing(QueryBalance(customer_id="c1"), ResponseBalance)

assert first == again == ResponseBalance(customer="Ana", balance=100)
assert computed == ["c1"]
assert caching.depends_on(QueryBalance(customer_id="c1")) == set()  # kept per tenant: asked outside it
```

`on` registers an interceptor around the Query, so it is decided before the bus is built and
reaches the Query however it is executed — HTTP, MCP, another ApplicationService. Only Queries:
a Command decides from the database, never from a cache.

**The key** is the bus, the Query and its values written canonically, the hash of the response's
JSON Schema — a deploy that changes the answer's shape never reads the old one — and the context
keys the policy varies by. A context key named `sensitive` that the policy does not vary by is
refused: two users never share an answer.

## What an answer depends on: what it read

The repository notes every aggregate a unit of reading touched — `get`, `search`, `count`,
relations, a hand-written SQL statement by its tables — and those are the answer's tags. Each tag
has a version in the store; invalidating it is one `increment`, and an answer whose tags moved is
not served.

```python
with billing.context({"tenant_id": "acme"}):
    repository.save(Invoice(id="i2", customer_id="c1", total=50))
    caching.invalidate(Invoice)                # what a commit does for you: see below
    assert billing(QueryBalance(customer_id="c1"), ResponseBalance).balance == 150

assert computed == ["c1", "c1"]
```

On SQL, `invalidate_on_commit(database, caching)` invalidates every aggregate a commit wrote —
after the commit, never at the flush, never on a rollback. Writes of other processes arrive as
their domain events: `caching.invalidated_by({InvoiceIssued: [Invoice]})` is a bus to hand to the
`Subscriber` beside the others. An answer that read nothing a repository noted, and declares no
`depends_on`, is answered and never kept — there would be nothing to let it go by.

## How long, and what happens near the end

```python
policy = CachePolicy(
    ttl=timedelta(minutes=5),            # the safety net — invalidation is the mechanism
    jitter=0.1,                          # spread ±10 %, so answers kept together expire apart
    stale_for=timedelta(minutes=1),      # served past the ttl while one caller recomputes
    early_expiry=1.0,                    # XFetch: recompute early, likelier as expiry nears
    wait_for_others=timedelta(seconds=2),
)
```

One caller recomputes a missing or expiring answer — whoever wins the store's atomic `add` on
its lock; the others serve the stale answer, or wait a moment for the new one and compute it
themselves if it does not come. `QueryCaching(store, near=timedelta(seconds=5))` keeps answers in
the process as well, and still checks their tag versions in the store, so an invalidation from
another replica is seen at once.

## Several contexts, one store — and turning it off

```python
shared = InMemoryKeyValue()
billing_cache = QueryCaching(shared, namespace="billing")
catalog_cache = QueryCaching(shared, namespace="catalog")
billing_cache.invalidate()  # billing's answers only; catalog's stay
billing_cache.enabled = False  # every billing query runs its use case, nothing kept

assert [name.rsplit(".", 1)[-1] for name in caching.policies()] == ["QueryBalance"]
```

A namespace is a cache of its own: its answers and its tags carry it, so letting go of one
context's answers never touches another's. Contexts that read the same aggregates and must see
each other's writes share a namespace. `enabled = False` rules the cache out — chasing a wrong
answer, or in an incident — without recomposing anything; `policies()` says what is cached and how.

## Any value, not only a Query: `Cache`

`QueryCaching` knows when to let go because the repository saw what an answer read. What another
system answers — a tenant a token resolves to, a catalog a service publishes — gives no such
signal; what it gives is a version. `Cache` keeps a value by its parameters and judges it by the
strategies each call chooses in its `KeepPolicy`: a **freshness** (how long it is served as is),
a **validation** (whether it is still right) and a **failure policy** (what is answered when the
source fails).

```python
from sincpro_framework.data_layer.caching import Cache, ExternalVersion, JsonCodec, KeepPolicy


class Tenant(DataTransferObject):
    name: str
    version: str


class Registry:
    """Another system: resolving is the expensive call, its version the cheap one."""

    def __init__(self) -> None:
        self.version = "v1"
        self.resolved: list[str] = []

    def resolve(self, token: str) -> Tenant:
        self.resolved.append(token)
        return Tenant(name=f"tenant-of-{token}", version=self.version)

    def current_version(self) -> str:
        return self.version


def version_of(tenant: Tenant) -> str:
    return tenant.version


registry = Registry()
tenants = Cache()  # no store: objects in this process, never encoded
by_version = KeepPolicy[Tenant](
    validation=ExternalVersion(
        registry.current_version, kept=version_of, trusted_for=timedelta(minutes=5)
    )
)


def tenant_of(token: str) -> Tenant:
    return tenants.get_or_compute(("tenant", token), lambda: registry.resolve(token), by_version)


assert tenant_of("tok") == tenant_of("tok")
assert registry.resolved == ["tok"]
```

**The key** is the parameters, written canonically and hashed — a token used as a key is never
readable in the store's key names. **`ExternalVersion`** is an ETag: the value keeps the version
it was computed at (`kept` reads it off the value, or it is asked before computing), a check that
held is trusted for `trusted_for`, and a version that moved means the value is recomputed — never
served, stale window or not. `Unconditional()`, the default, leaves the freshness as the whole
answer.

On a store replicas share, values are bytes, so the call names its `Codec`:

```python
from sincpro_framework.data_layer.caching import TimeToLive

catalogs = Cache(InMemoryKeyValue(), namespace="catalog")   # RedisKeyValue(...) on replicas
five_minutes = KeepPolicy[Tenant](
    freshness=TimeToLive(ttl=timedelta(minutes=5), jitter=0.1, stale_for=timedelta(minutes=1))
)
shared = catalogs.get_or_compute(
    ("catalog", "acme"), lambda: registry.resolve("acme"), five_minutes, JsonCodec(Tenant)
)
catalogs.forget(("catalog", "acme"), JsonCodec(Tenant))      # here and on every replica
```

One caller computes a missing or expiring value — the winner of the store's `add`, or of a lock
in the process — while the others serve the stale value or wait `wait_for_others` for the new one.
An exception is never kept, and a value larger than `max_bytes` is answered and not kept.

### How long: `TimeToLive` or `Sliding`

`TimeToLive(ttl=, jitter=, stale_for=, early_expiry=)` is the time-to-live every value had so far
(`Lifetime` is the same class under its phase-1 name): the ttl spread by `jitter`, served stale
for `stale_for` while one caller recomputes, recomputed early by XFetch's `early_expiry`.
`Sliding(idle_for=, at_most=)` keeps a value served for as long as somebody asks for it — fresh for
`idle_for` after the last hit, never past `at_most` after it was computed, so a hot value is still
recomputed. On a shared store a hit renews it at most once per quarter of its window: a sliding
value is not a write per read.

```python
from datetime import UTC, datetime

from sincpro_framework.data_layer.caching import CacheOutcome, CountingObserver, FailSafe, Sliding
from sincpro_framework.runtime.testing import ManualClock

clock = ManualClock(datetime(2026, 9, 29, 12, 0, tzinfo=UTC))
observed = CountingObserver()
sessions = Cache(now=clock.now, namespace="sessions", observer=observed)
while_active = KeepPolicy[Tenant](
    freshness=Sliding(idle_for=timedelta(minutes=10), at_most=timedelta(hours=1))
)

opened: list[str] = []


def session_of(token: str) -> Tenant:
    opened.append(token)
    return Tenant(name=f"session-of-{token}", version="v1")


for _ in range(6):                    # a hit every 5 minutes: never sent to the source again
    sessions.get_or_compute(("session", "tok"), lambda: session_of("tok"), while_active)
    clock.advance(minutes=5)

assert opened == ["tok"]
```

### When the source fails: `FailSafe`

A source that goes down turns every call into an error the moment its value lapses — although the
value was right a minute ago. `FailSafe(serve_for=, throttle_for=30s, errors=Exception)` is
RFC 5861's `stale-if-error`: when computing raises one of `errors`, the last good value is served
for up to `serve_for` past its servable life, and re-kept fresh for `throttle_for`, so the source
is asked once per window, not once per call. It never serves a value its validation *rejected* —
fail-safe covers a source that cannot answer, not one that answered no — and a validation that
raises means "cannot tell", which the policy handles. `Raise()`, the default, serves nothing past
the value's servable life: stale data is a choice, never a surprise.

```python
class DownRegistry(Registry):
    def resolve(self, token: str) -> Tenant:
        if self.version == "down":
            raise ConnectionError("the registry is not answering")
        return super().resolve(token)


flaky = DownRegistry()
fail_safe = KeepPolicy[Tenant](
    freshness=TimeToLive(ttl=timedelta(minutes=5)),
    failure=FailSafe(serve_for=timedelta(hours=1), errors=[ConnectionError, TimeoutError]),
)
guarded = Cache(now=clock.now, namespace="tenants", observer=observed)
before = guarded.get_or_compute("acme", lambda: flaky.resolve("acme"), fail_safe)

flaky.version = "down"
clock.advance(minutes=6)
during = guarded.get_or_compute("acme", lambda: flaky.resolve("acme"), fail_safe)

assert during == before
assert observed.of(CacheOutcome.FALLBACK, "tenants") == 1
```

### Every call says what it did

Each call reports one outcome to the cache's `observer` — `HIT`, `STALE`, `COMPUTED`, `COALESCED`,
`INVALIDATED` (a kept value its validation rejected) or `FALLBACK` — plus `BYPASSED` when the
shared store was left out. By default each is an event on the active span (`SpanObserver`) and one more on the
`sincpro.cache.outcomes` metric (`MetricsObserver`, see `docs/observability/metrics.md`);
`CountingObserver` counts them for a metrics exporter. Without `FALLBACK` and `BYPASSED` counted,
fail-safe and a failing store hide an outage. An observer that raises never breaks the call.

### When the shared store fails, and how big the process tier gets

Any exception from the shared store opens its breaker for `bypass_for` (30 s by default): the call
is answered on the process tier — still one caller per key — and reports `BYPASSED`; the first
call after the window tries the store again. A failure to *write* never fails the call: the value
is answered and the breaker trips. How long one operation may take is the client's socket timeout
— configure it on the Redis client you hand `RedisKeyValue`. `forget` is the exception: an
invalidation that reached no replica raises, so whoever asked for it knows.

The process tier keeps objects in memory, bounded by an `Eviction`: `Lru(max_entries=10_000)` by
default, `Unbounded()` for a tier whose keys are known and few, or yours behind the `Eviction`
port.

```python
from sincpro_framework.data_layer.caching import Lru

bounded = Cache(eviction=Lru(max_entries=2))
for customer in ("c1", "c2", "c3"):
    bounded.get_or_compute(("balance", customer), lambda: 0)

assert len(bounded._process._kept) == 2           # c1, the least recently used, went
```

## A write that runs once: `Idempotency`

A confirmed write whose answer is lost on the way back looks, to the client, like one that never
ran — and the retry carries the same payload, often while the first is still running, on another
replica. *Check, run, remember* lets both through. `Idempotency` claims the key with the store's
atomic `add` first: exactly one caller runs, a completed answer is replayed, a failure releases the
claim so the retry runs.

```python
from sincpro_framework import Feature, UseFramework
from sincpro_framework.data_layer.caching import (
    AlreadyInProgress,
    Idempotency,
    IdempotencyPolicy,
    KeyReused,
)


class CommandIssueReceipt(DataTransferObject):
    request_id: str
    total: int

    def idempotency_key(self) -> str:         # the Command says what identifies one request
        return self.request_id


class ResponseIssueReceipt(DataTransferObject):
    number: int


receipts = UseFramework("receipts", log_after_execution=False)
idempotency = Idempotency(InMemoryKeyValue())                # RedisKeyValue(...) on replicas
receipts.ignore_sentry_exceptions(AlreadyInProgress, KeyReused)  # duplicates are not bugs
issued: list[int] = []


@receipts.feature(CommandIssueReceipt)
@idempotency.once(
    expires_after=timedelta(minutes=2),       # a transport retry, not a person on purpose
    in_progress_for=timedelta(minutes=1),     # a claim left by a dead replica expires
    wait_for_completion=timedelta(seconds=5), # a duplicate mid-run waits for the answer
    vary_by="tenant_id",                   # two tenants never share an answer
)
class IssueReceipt(Feature):
    def execute(self, dto: CommandIssueReceipt) -> ResponseIssueReceipt:
        issued.append(dto.total)
        return ResponseIssueReceipt(number=len(issued))


with receipts.context({"tenant_id": "acme"}):
    first = receipts(CommandIssueReceipt(request_id="r-1", total=10), ResponseIssueReceipt)
    retry = receipts(CommandIssueReceipt(request_id="r-1", total=10), ResponseIssueReceipt)
    try:
        receipts(CommandIssueReceipt(request_id="r-1", total=99), ResponseIssueReceipt)
    except KeyReused:
        pass

assert first == retry and issued == [10]
```

A key reused for a different payload is `KeyReused` (the IETF `Idempotency-Key` draft's 422); a
duplicate still in flight past `wait_for_completion` is `AlreadyInProgress` (its 409). Both are
`DomainError`s — expected traffic: list them in the context's `ignore_sentry_exceptions(...)`.

The Command says what identifies one request with a plain method, `idempotency_key()`: a rename
moves it, a type checker checks it, and the DTO is an ordinary DTO. The fields not in the key are
still compared — `r-1` with another total is `KeyReused`. A Command without the method is keyed
by every field.

**What it needs underneath** is a place replicas share to claim a key and keep its answer — not
a cache. On a `KeyValueStore` (Redis/Valkey) that is `KeyValueRecords`, and "once" is best effort
for one case: a replica that dies after its side effect and before recording the answer. For
that case, implement `IdempotencyRecords` over your database so the record commits in the same
transaction as the write, and prove it with `IdempotencyRecordsContract`. Inside the write,
`current_idempotency_key()` is the key to hand on to another system that deduplicates by key.

`once(...)` wraps the use case's own `execute`: nothing is wired on the bus, and the use case
still runs inside it — Sentry, its span and the access guard, so a replayed answer is still
authorized. A subclass that keeps `execute` keeps `once()`; a `replaces=` handler with an
`execute` of its own declares its own. An async caller reaches it with `bus.get_async_bus()`,
which runs the synchronous `execute` — claim first — on a worker thread, so duplicates fanned out
with `asyncio.gather` still write once; an `async def execute` is refused, by `once()` and by the
bus alike.

## Providers

`QueryCaching` keeps a Query's answer for its bus, in a store replicas share; the rows of a read
held for analysis in one process are `QueryCache`, in [data analysis](../data_analysis/README.md).


```python
from sincpro_framework.runtime.testing import KeyValueStoreContract
from sincpro_framework.common.store import KeyValueStore


class DictStore(InMemoryKeyValue):
    """A store of yours: here the one in memory, standing in for DynamoDB or a table."""


class TestDictStore(KeyValueStoreContract):
    def make_store(self) -> KeyValueStore:
        return DictStore()

    def expire(self, store: KeyValueStore, seconds: float) -> None:
        import time

        time.sleep(seconds)
```

A codec, a freshness or an eviction of yours proves itself the same way, with the suites the
built-ins pass:

```python
from sincpro_framework.data_layer.caching import Eviction, Freshness, Unbounded
from sincpro_framework.runtime.testing import CodecContract, EvictionContract, FreshnessContract


class TestTenantCodec(CodecContract):
    def make_codec(self) -> JsonCodec[Tenant]:
        return JsonCodec(Tenant)

    def samples(self) -> list[Tenant]:
        return [Tenant(name="acme", version="v1")]


class TestSessions(FreshnessContract):
    def make_freshness(self) -> Freshness:
        return Sliding(idle_for=timedelta(minutes=10), at_most=timedelta(hours=1))


class TestFewKeys(EvictionContract):
    bound = None                      # unbounded: the bound test holds trivially

    def make_eviction(self) -> Eviction:
        return Unbounded()
```

```bash
pip install sincpro-framework[redis]        # RedisKeyValue(redis.Redis.from_url(url)) — Valkey too
pip install sincpro-framework[memcached]    # MemcachedKeyValue(pymemcache.Client(address))
```

The same operations carry crons across replicas:

```python
from datetime import UTC, datetime

from sincpro_framework.entrypoints.adapters.cron import KeyValueRuns

shared = InMemoryKeyValue()                   # RedisKeyValue(...) on real replicas
one, other = KeyValueRuns(shared), KeyValueRuns(shared)
tick = datetime(2026, 9, 27, 2, 0, tzinfo=UTC)
assert one.claim("close-books", tick) and not other.claim("close-books", tick)
```

| | |
|---|---|
| `KeyValueStore` | `get_many`, `set`, `add` (atomic), `increment` (atomic), `delete`, `take` (atomic), `get` |
| `InMemoryKeyValue(now=)` / `RedisKeyValue(client, prefix=)` / `MemcachedKeyValue(client, prefix=)` | the providers |
| `QueryCaching(store, near=, sensitive=, namespace=, enabled=)` | `.on(bus, Query, CachePolicy(...))`, `.invalidate(Aggregate)`, `.invalidated_by({Event: [Aggregate]})`, `.depends_on(query)`, `.policies()` |
| `Cache(store=None, namespace=, enabled=, observer=, eviction=, bypass_for=)` | `.get_or_compute(key, compute, KeepPolicy(freshness=, validation=, failure=, wait_for_others=, max_bytes=), codec)`, `.forget(key)` |
| `TimeToLive(ttl=, jitter=, stale_for=, early_expiry=)` (alias `Lifetime`) / `Sliding(idle_for=, at_most=)` / `Freshness` | how long a value is served as is — yours inherits `Freshness` |
| `Unconditional()` / `ExternalVersion(current, kept=, trusted_for=)` / `Validation` | whether it is still right — yours inherits `Validation` |
| `Raise()` (default) / `FailSafe(serve_for, throttle_for=, errors=)` / `FailurePolicy` | what a failing source answers |
| `Lru(max_entries=10_000)` (default) / `Unbounded()` / `Eviction` | how the process tier stays bounded |
| `Idempotency(store \| records, namespace=, observer=)` | `@.once(expires_after=, in_progress_for=, wait_for_completion=, vary_by=)`, `.run(key, write, IdempotencyPolicy(...), codec, payload=)`, `.policies()`; `AlreadyInProgress`, `KeyReused`; `current_idempotency_key()`; `declares_once(cls)` — whether a class runs once |
| `IdempotentCommand` | a Command with `idempotency_key()` — what identifies one request |
| `IdempotencyRecords` / `KeyValueRecords(store)` | where records live — yours transactional, or on a key-value store |
| `CacheObserver` / `SpanObserver` + `MetricsObserver` (default, via `Observers`) / `CountingObserver` / `NoObserver` | what each call did: `CacheOutcome`, `IdempotencyOutcome` |
| `JsonCodec(shape)` / `Codec` | a value as the bytes a shared store keeps |
| `invalidate_on_commit(database, caching)` | every aggregate a commit wrote |
| `KeyValueRuns(store)` | crons on several replicas |
| `KeyValueStoreContract` | the tests a store of yours inherits |
| `CodecContract` / `FreshnessContract` / `EvictionContract` (`sincpro_framework.runtime.testing`) | the tests a codec, a freshness or an eviction of yours inherits |
