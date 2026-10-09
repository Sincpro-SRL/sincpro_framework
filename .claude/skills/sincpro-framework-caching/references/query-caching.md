# QueryCaching

Deeper, in the framework repo: `docs/caching/README.md`.

```python
from datetime import timedelta

from sincpro_framework.caching import CachePolicy, InMemoryKeyValue, QueryCaching

caching = QueryCaching(InMemoryKeyValue(), sensitive=("user_id",))
caching.on(billing, QueryBalance, CachePolicy(ttl=timedelta(minutes=5), vary_by="tenant_id"))
# a call with `user_id` in its context is refused: this policy does not vary by it

with billing.context({"tenant_id": "acme"}):
    first = billing(QueryBalance(customer_id="c1"), ResponseBalance)
    again = billing(QueryBalance(customer_id="c1"), ResponseBalance)
assert first == again
```

`on` registers an interceptor around the Query, so it is decided **before the bus is built** and
reaches the Query however it is executed — HTTP, MCP, another ApplicationService. It needs the
handler already registered (`TypeError` otherwise) and its `execute` to declare a return type;
after the bus is built it is refused (`BusAlreadyBuilt`). **Only Queries** — nothing refuses a
Command, but a Command decides from the database, never a cache.

## The key

The bus, the Query and its values written canonically, the hash of the response's JSON Schema — a
deploy that changes the answer's shape never reads the old one — and the context keys the policy
varies by — **context keys only**. The authenticated identity is not in the context unless something
puts it there, so a cache that must not cross tenants gets the key from a context provider:

```python
from sincpro_framework.auth.security_context import current_identity

@billing.context_provider(gives=["tenant_id"])
def tenant_of_the_caller(context):
    return {"tenant_id": current_identity().tenant}
```

A context key named in `sensitive` that the policy does not vary by makes that call **refused**
(`ContractViolation`) — checked on every call, not when the policy is registered: two users never
share an answer.

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
kept — there would be nothing to let it go by (a warning on the `sincpro_framework.caching`
logger on every such call). A Query that reads through an HTTP adapter or a raw connection declares what it depends on:

```python
CachePolicy(ttl=timedelta(minutes=5), depends_on=[Invoice])   # invalidating Invoice lets it go
```

A write that does not reach `invalidate`, `invalidate_on_commit` or `invalidated_by` — a
`MemoryRepository` save, a script on a raw session, another service — leaves answers stale until
their ttl. `invalidate(Invoice)` also invalidates `Invoice`'s base classes; `invalidate()` with no
argument lets go of every answer of this namespace.

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
billing_cache.policies()          # what is cached and how
```

A namespace is a cache of its own. Contexts that read the same aggregates and must see each other's
writes share a namespace.

## Related API

`QueryCaching(store, near=, sensitive=, now=, namespace=, enabled=)`: `.on(bus, Query, CachePolicy)`,
`.invalidate(Aggregate | None)`, `.invalidated_by({Event: [Aggregate]})`, `.depends_on(query)` (the
tags the answer held for that query instance depends on), `.policies()`.

`CachePolicy(ttl, vary_by=(), stale_for=0, jitter=0.1, early_expiry=1.0, depends_on=(),
max_bytes=1 MiB, wait_for_others=2 s)`; `ttl` is required.
