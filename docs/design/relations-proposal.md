# Relations: a proposal, not yet built

Status: **design for review.** Nothing below exists in the code. `Meta.relations` is derived from
the annotations today and nothing resolves it; `Criteria` has no `resolve`. This page is what the
persistence layer would grow to answer «bring the related records too», and it is written before
the code so the shape can be argued about without a refactor.

---

## 1. The three cases are one declaration

A relation is declared once, on the aggregate, with its cardinality in the annotation and its
mechanics in the mapping. The caller never knows which of the three it is.

| Case | Where the other side lives | Declared as | Resolved by |
|---|---|---|---|
| **Foreign key, same database** — the common case once databases are unified | the same `MetaData` | `relationship()` in `properties`, as SQLAlchemy always did | `selectinload`; one extra `SELECT … IN (…)` per relation per level. Lazy loading stays available for a Feature inside a unit of work, and is refused on a detached record |
| **Key list in a JSON column** — `derived_from: list[str]` | the same database, no FK | `Relation.local(key="derived_from", target=Dataset)` | one `browse` per level, in the same session |
| **Another context, another database, another process** | anywhere | `Relation.remote("execution:run", local="dataset_id", remote="dataset_id")` | a `Resolver` registered for `"execution:run"` |

```python
@dataclass
class Dataset(Entity):
    runs: list["Run"] = field(default=UNRESOLVED, metadata=relation(remote("execution:run", local="dataset_id", remote="dataset_id")))
    sources: list["Dataset"] = field(default=UNRESOLVED, metadata=relation(local(key="derived_from")))
```

`Meta.relations` publishes `name → {target, many, kind}` so a client knows what it may ask to
resolve, exactly as it knows what it may filter.

---

## 2. The caller asks with `resolve`, and gets `included`

```python
Criteria(resolve={"runs": Criteria(order=parse_order("-started_at"), pagination=Pagination(limit=5))})
```

- `resolve` maps a relation name to a criteria for the related set: filter, order and page
  **inside** each parent's relation. Depth is bounded at 3, each relation has a default and a
  maximum limit, and an undeclared path is refused before the database is touched.
- The engine resolves **level by level, never per row**: one call per relation per level, ids in
  chunks of ~500. For twenty parents with four levels that is four queries, not ninety-four.
- Two shapes come back, both true at once. The record's attribute is populated, so a Feature writes
  `dataset.runs`. And `EntityCollection.included` carries each related set as its own `EntityCollection`, keyed
  by relation name, so the wire keeps its types: `items` plus `included`, never a nested object
  whose shape depends on what was asked.
- Touching a relation nobody asked for raises `RelationNotResolved`. On a server, a loop over two
  hundred rows is two hundred queries, and the code reads identically to a free attribute access.
  Raising puts the cost in the declaration, where a reviewer sees it.

---

## 3. `Resolver` is a Protocol, and this time it is earned

```python
class Resolver(Protocol):
    def resolve(self, keys: Sequence[Any], criteria: Criteria) -> dict[Any, EntityCollection]: ...
```

Given the parents' keys and the per-relation criteria, it answers a collection per key. Four
implementations are on the table, which is why an interface is justified here and nowhere else in
the layer:

| Resolver | How | When |
|---|---|---|
| `BusResolver(bus, CommandBrowseRuns)` | executes a Command on the other context's bus with the key list; the Command and Response are that context's own | two contexts in one process, the normal case |
| `HttpResolver(client, url)` | one request with the key list, a page of records back | another service |
| `SqlResolver(database, statement)` | a `Select` the provider writes, run against another `Database` | a legacy schema, a reporting replica |
| `EventProjection` | not a resolver: a stored, denormalised field kept current by a `DomainEvent` | when the relation must be **filtered or sorted by**, not just shown |

The provider registers resolvers in `dependencies.py`, next to the repository:

```python
repository.resolvers.register("execution:run", BusResolver(execution, CommandBrowseRuns, key="dataset_id"))
```

---

## 4. The rule that keeps this from becoming a distributed N+1

A resolver answers **a detail view**: this dataset, its five latest runs. It is one call per
relation per level and it runs when a page is read.

A resolver does **not** answer a listing that filters or sorts by the other side: «datasets with
runs», «invoices whose customer is in Cochabamba». That question needs the value **in the
aggregate's own table**, kept current by an event — `runs_count` written when `RunStarted`
arrives. Odoo does not have this problem because it is one database; a system that is several
does, and the answer is the event, not a smarter resolver.

Both mechanisms together cover the two questions. Either alone breaks on the other.

---

## 5. What is deliberately out

- No cross-database join. A join is a same-database thing; the resolver is how the other cases
  compose, and `repository.session` is how a same-database join is written by hand.
- No field mask on the parent. `resolve` selects relations; the parent's own fields travel whole.
- No relation in `where`. Filtering by the other side is the projection's job (§4).

## 6. The proof

Map `Run` in `sincpro_synthesis` and resolve `dataset → runs` with a `BusResolver` across two
SQLite files. Then unify the two databases and switch the declaration to a foreign key without
touching the Feature that reads it. If both work without a change to the layer, the design closes.
