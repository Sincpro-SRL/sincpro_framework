# QueryCaching

Depth: `docs/caching/README.md`. Every example runs as a test.

```python
from sincpro_framework.caching import CachePolicy, InMemoryKeyValue, QueryCaching

caching = QueryCaching(InMemoryKeyValue(), sensitive=("user_id",))
caching.on(billing, QueryBalance, CachePolicy(ttl=timedelta(minutes=5), vary_by=("tenant_id",)))

with billing.context({"tenant_id": "acme"}):
    first = billing(QueryBalance(customer_id="c1"), ResponseBalance)
    again = billing(QueryBalance(customer_id="c1"), ResponseBalance)
assert first == again
```

`on` registers an interceptor around the Query, so it is decided **before the bus is built** and
reaches the Query however it is executed — HTTP, MCP, another ApplicationService. **Only Queries**
(a Command decides from the database, never a cache).

## The key

The bus, the Query and its values written canonically, the hash of the response's JSON Schema — a
deploy that changes the answer's shape never reads the old one — and the context keys the policy
varies by. A context key named `sensitive` that the policy does not vary by is **refused**: two users
never share an answer.

## Invalidation is the mechanism; ttl is the safety net

The repository notes every aggregate a unit of reading touched (`get`, `search`, `count`, relations,
a hand-written statement by its tables) — those are the answer's **tags**. Each tag has a version in
the store; invalidating is one `increment`, and an answer whose tags moved is not served.

```python
with billing.context({"tenant_id": "acme"}):
    repository.save(Invoice(id="i2", customer_id="c1", total=50))
    caching.invalidate(Invoice)
```

On SQL, `invalidate_on_commit(database, caching)` invalidates every aggregate a commit wrote —
**after the commit**, never at the flush, never on a rollback. Writes of other processes arrive as
their domain events: `caching.invalidated_by({InvoiceIssued: [Invoice]})` is a bus to hand the
`Subscriber` beside the others.

An answer that read nothing a repository noted, and declares no `depends_on`, is answered and never
kept — there would be nothing to let it go by.

## `CachePolicy`

```python
policy = CachePolicy(
    ttl=timedelta(minutes=5),
    jitter=0.1,                          # spread ±10 %
    stale_for=timedelta(minutes=1),      # served past the ttl while one caller recomputes
    early_expiry=1.0,                    # XFetch
    wait_for_others=timedelta(seconds=2),
)
```

One caller recomputes a missing or expiring answer — whoever wins the store's atomic `add` on its
lock; the others serve the stale answer or wait a moment. `QueryCaching(store,
near=timedelta(seconds=5))` also keeps answers in-process and still checks tag versions in the store,
so an invalidation from another replica is seen at once.

## Several contexts, one store

```python
shared = InMemoryKeyValue()
billing_cache = QueryCaching(shared, namespace="billing")
catalog_cache = QueryCaching(shared, namespace="catalog")
billing_cache.invalidate()        # billing's answers only
billing_cache.enabled = False     # every billing query runs its use case, nothing kept
caching.policies()                # what is cached and how
```

A namespace is a cache of its own. Contexts that read the same aggregates and must see each other's
writes share a namespace.

## Related API

`QueryCaching(store, near=, sensitive=, namespace=, enabled=)`: `.on(bus, Query, CachePolicy)`,
`.invalidate(Aggregate)`, `.invalidated_by({Event: [Aggregate]})`, `.depends_on(query)`,
`.policies()`.
