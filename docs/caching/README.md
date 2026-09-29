# Caching — infrastructure the use case never sees

A cache is decided where a bounded context is composed, like its repository or its queue. A
Feature or an ApplicationService is written exactly as without one; whoever wires the context
says which Queries keep their answers, for how long, per what, and on which store.

- **`KeyValueStore`** is the specification: the operations every provider has. The core ships
  `InMemoryKeyValue` (standard library only); `RedisKeyValue` (Redis and Valkey) and
  `MemcachedKeyValue` take the client you build, behind the `[redis]` and `[memcached]` extras.
  A store of yours proves itself with `sincpro_framework.testing.KeyValueStoreContract`.
- **`QueryCaching`** keeps a Query's answer and lets go of it when an aggregate it read is
  written — the repository notes the reads, nobody declares them.
- **`Cache`** keeps any value by its parameters — what another system answered, a computation —
  judged by the strategies of each call: a `Lifetime`, and a `Validation` such as the version the
  source publishes. No ORM, no Query.
- **`Idempotency`** runs a write once per key across replicas, and replays its answer to a retry.
- **`KeyValueRuns`** is the record several cron replicas share: one of them runs each tick.

Every block on this page runs, in order, in `tests/docs/test_persistence_guide.py`.

## A use case that knows nothing

```python
from dataclasses import dataclass
from datetime import timedelta

from sincpro_framework import ApplicationService, DataTransferObject, UseFramework
from sincpro_framework.ddd import Criteria, Entity, MemoryRepository


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
from sincpro_framework.caching import CachePolicy, InMemoryKeyValue, QueryCaching

caching = QueryCaching(InMemoryKeyValue(), sensitive=("user_id",))
caching.on(billing, QueryBalance, CachePolicy(ttl=timedelta(minutes=5), vary_by=("tenant_id",)))

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
signal; what it gives is a version. `Cache` keeps a value by its parameters and judges it by two
strategies each call chooses: a **lifetime** (how long it is served as is) and a **validation**
(whether it is still right).

```python
from sincpro_framework.caching import Cache, ExternalVersion, JsonCodec, KeepPolicy, Lifetime


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
served, stale window or not. `Unconditional()`, the default, leaves the lifetime as the whole
answer.

On a store replicas share, values are bytes, so the call names its `Codec`:

```python
catalogs = Cache(InMemoryKeyValue(), namespace="catalog")   # RedisKeyValue(...) on replicas
five_minutes = KeepPolicy[Tenant](
    lifetime=Lifetime(ttl=timedelta(minutes=5), jitter=0.1, stale_for=timedelta(minutes=1))
)
shared = catalogs.get_or_compute(
    ("catalog", "acme"), lambda: registry.resolve("acme"), five_minutes, JsonCodec(Tenant)
)
catalogs.forget(("catalog", "acme"), JsonCodec(Tenant))      # here and on every replica
```

One caller computes a missing or expiring value — the winner of the store's `add`, or of a lock
in the process — while the others serve the stale value or wait `wait_for_others` for the new one.
An exception is never kept, and a value larger than `max_bytes` is answered and not kept.

## A write that runs once: `Idempotency`

A confirmed write whose answer is lost on the way back looks, to the client, like one that never
ran — and the retry carries the same payload, often while the first is still running, on another
replica. *Check, run, remember* lets both through. `Idempotency` claims the key with the store's
atomic `add` first: exactly one caller runs, a completed answer is replayed, a failure releases the
claim so the retry runs.

```python
from sincpro_framework import Feature, UseFramework
from sincpro_framework.caching import (
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
    vary_by=("tenant_id",),                   # two tenants never share an answer
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
`execute` of its own declares its own.

## Providers

`QueryCaching` keeps a Query's answer for its bus, in a store replicas share; the rows of a read
held for analysis in one process are `QueryCache`, in [data analysis](../data_analysis/README.md).


```python
from sincpro_framework.testing import KeyValueStoreContract
from sincpro_framework.caching import KeyValueStore


class DictStore(InMemoryKeyValue):
    """A store of yours: here the one in memory, standing in for DynamoDB or a table."""


class TestDictStore(KeyValueStoreContract):
    def make_store(self) -> KeyValueStore:
        return DictStore()

    def expire(self, store: KeyValueStore, seconds: float) -> None:
        import time

        time.sleep(seconds)
```

```bash
pip install sincpro-framework[redis]        # RedisKeyValue(redis.Redis.from_url(url)) — Valkey too
pip install sincpro-framework[memcached]    # MemcachedKeyValue(pymemcache.Client(address))
```

The same operations carry crons across replicas:

```python
from datetime import UTC, datetime

from sincpro_framework.cron import KeyValueRuns

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
| `Cache(store=None, namespace=, enabled=)` | `.get_or_compute(key, compute, KeepPolicy(lifetime=, validation=, wait_for_others=, max_bytes=), codec)`, `.forget(key)` |
| `Lifetime(ttl=, jitter=, stale_for=, early_expiry=)` | how long a value is served as is |
| `Unconditional()` / `ExternalVersion(current, kept=, trusted_for=)` / `Validation` | whether it is still right — yours inherits `Validation` |
| `Idempotency(store \| records, namespace=, observer=)` | `@.once(expires_after=, in_progress_for=, wait_for_completion=, vary_by=)`, `.run(key, write, IdempotencyPolicy(...), codec, payload=)`, `.policies()`; `AlreadyInProgress`, `KeyReused`; `current_idempotency_key()` |
| `IdempotentCommand` | a Command with `idempotency_key()` — what identifies one request |
| `IdempotencyRecords` / `KeyValueRecords(store)` | where records live — yours transactional, or on a key-value store |
| `CacheObserver` / `SpanObserver` (default) / `CountingObserver` / `NoObserver` | what each call did: `CacheOutcome`, `IdempotencyOutcome` |
| `JsonCodec(shape)` / `Codec` | a value as the bytes a shared store keeps |
| `invalidate_on_commit(database, caching)` | every aggregate a commit wrote |
| `KeyValueRuns(store)` | crons on several replicas |
| `KeyValueStoreContract` | the tests a store of yours inherits |
