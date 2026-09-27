# Caching — infrastructure the use case never sees

A cache is decided where a bounded context is composed, like its repository or its queue. A
Feature or an ApplicationService is written exactly as without one; whoever wires the context
says which Queries keep their answers, for how long, per what, and on which store.

- **`KeyValueStore`** is the specification: six operations every provider has. The core ships
  `InMemoryKeyValue` (standard library only); `RedisKeyValue` (Redis and Valkey) and
  `MemcachedKeyValue` take the client you build, behind the `[redis]` and `[memcached]` extras.
  A store of yours proves itself with `sincpro_framework.testing.KeyValueStoreContract`.
- **`QueryCaching`** keeps a Query's answer and lets go of it when an aggregate it read is
  written — the repository notes the reads, nobody declares them.
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

## Providers

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

The same six operations carry crons across replicas:

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
| `KeyValueStore` | `get_many`, `set`, `add` (atomic), `increment` (atomic), `delete`, `get` |
| `InMemoryKeyValue(now=)` / `RedisKeyValue(client, prefix=)` / `MemcachedKeyValue(client, prefix=)` | the providers |
| `QueryCaching(store, near=, sensitive=)` | `.on(bus, Query, CachePolicy(...))`, `.invalidate(Aggregate)`, `.invalidated_by({Event: [Aggregate]})`, `.depends_on(query)` |
| `invalidate_on_commit(database, caching)` | every aggregate a commit wrote |
| `KeyValueRuns(store)` | crons on several replicas |
| `KeyValueStoreContract` | the tests a store of yours inherits |
