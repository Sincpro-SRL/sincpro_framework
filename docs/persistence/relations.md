# Relations

How an aggregate reaches another one: the model, the rules that decide what a name is, the
algorithm that brings the other side once per page, what it costs, and how to add a new way to
resolve. This is the longest page because it is the part with the most ways to get wrong.

## 1. The problem

`Dataset` has runs. Runs live in the same table today, in another database tomorrow, behind
another bounded context's bus next year. A screen wants twenty datasets with their five latest
failed runs, in one request. A Feature wants `dataset.runs` while it decides a rule. Neither
should know where runs live, and neither should turn twenty datasets into twenty queries.

Three forces pull on the design:

- **Transparency for the client**: it names the relation in the criteria and gets it back typed,
  whatever brings it.
- **Cost that does not depend on the number of rows**: one call per relation per page.
- **Composition**: the criteria for a relation is a criteria, so the other side, whatever it is,
  receives something it already understands.

## 2. The model

A relation is a field of the aggregate's definition, `FieldMeta`, with `kind = "relation"`:

| Attribute | Meaning | Example, `Dataset.runs` |
|---|---|---|
| `type` | cardinality and the side of the key: `many2one`, `one2many`, `many2many` | `one2many` |
| `relation` | the aggregate on the other side, by name | `Run` |
| `identified_by` | the column that ties the two: this aggregate's own for a `many2one`, the related aggregate's for a `one2many` | `dataset_id`, on `Run` |
| `definition` | the other side's definition, cut by the mask, once expanded | `Meta` of `Run` |

Two axes are separated on purpose. **What the relation is**, its shape, lives in `Meta` and is
what a client reads. **How it is brought**, its mechanics, lives in the data mapper as a
`Relation` and is what the resolver follows. `Work.reviews` from another context is a `one2many`
identified by `book_id` exactly like a same-database one; only the mechanics differ.

An **embedded value** is not a relation. A dataclass or pydantic model stored in a JSON column has
no identity, no life of its own and nothing to fetch: it is a field with `kind = "embedded"` and
a `definition` describing its shape. The mask cuts inside it; nothing resolves it. The line
between the two is lifecycle, not "simple versus compound".

## 3. What a name is: the precedence

`ModelIntrospection.describe()` and `map_aggregates` decide together, in this order, and nothing overrides anything
by surprise:

1. **A column with that name is a field.** Scalar, or embedded when its annotation is a value
   object. Never a relation, whatever the annotation says.
2. **A declaration wins over inference.** `map_aggregates(..., relations={Work: {"editor":
   TableRelation.foreign_key(Person, identified_by="editor_id")}})` is final for `editor`.
3. **A foreign key is inferred when it is unambiguous.** The annotation points at a mapped class
   and exactly one `ForeignKey` ties the two tables; with several, the column named
   `<attribute>_id` (to-one) or `<table>_id` (to-many) decides. Inference runs over the whole
   registry on every mapping call, so a class mapped later completes the relations of one mapped
   earlier. The match reads the column the key references: a `ForeignKey("ws.id")` matches the
   identity, a `ForeignKey("ws.code")` to a unique business key matches `code`, and the
   relation names both sides (`parent_field`, `related_field`) so the definition says so
   (`tests/data_layer/orm/test_relation_by_business_key.py`).
4. **A pointer nothing identifies is published, not expandable.** Annotation to a mapped class,
   no foreign key, no declaration: it appears with `identified_by = None`; asking for it drops
   with `not_expandable`.
5. **Everything else is ignored.** A dataclass with no table and no declaration is not a
   relation and not a field.

Cardinality always comes from the annotation: `EntityCollection[Run]` — the recommended type,
[§9](#9-a-to-many-is-an-entitycollection) — or `list[Run]` is many, `Run | None` is one. Only
`many_to_many` is a declared type, because a list cannot say there is a table in between.
`tests/data_layer/orm/test_relation_precedence.py` pins each rule.

## 4. Declaring what the tables cannot say

```python
map_aggregates(registry, {Author: author_table, Tag: tag_table, Work: work_table},
    relations={Work: {
        "tags":      TableRelation.many_to_many(Tag, through=work_tag, this_key="work_id", related_key="tag_id"),
        "sources":   TableRelation.id_list(Work, identified_by="source_ids"),            # ids in a JSON column
        "reviews":   TableRelation.bus(Review, reviews_bus, CommandSearchReviews, identified_by="book_id"),
        "publisher": TableRelation.resolved_by(Publisher, identified_by="publisher_id", resolver=fetch_publishers),
    }})
```

`Work.author` and `Author.works` are not there: the foreign key on `work.author_id` says them.

## 5. The algorithm

For a page of parents `P` and a named node `name: C`:

```
1. field = meta.fields[name]
   scalar with children → dropped(not_expandable); embedded → validate C.specification against its shape, done
2. relation = declared-or-inferred Relation for name; none → dropped(not_expandable)
3. groups = RESOLVE(relation.kind, P, C)              one call, whatever the kind
4. for each parent: parent.name = groups[parent.id]  (to-many → EntityCollection, to-one → record or None)
5. definition = RESOLVE-RELATIONS(children of all parents, C.specification)   recursion, all children at once
6. meta.fields[name] = field with identified_by and definition
```

`RESOLVE` by kind:

**Foreign key, to-many** (`Author.works`, key on the related table). On PostgreSQL, for each
parent its count and its first rows, each stopping where it has read enough:

```sql
SELECT children.*, counted.total
FROM (VALUES (:p1), (:p2), …) AS parents (key)
JOIN LATERAL (SELECT count(*) AS total FROM (SELECT 1 FROM work
              WHERE work.author_id = parents.key AND <C.where> LIMIT :cap + 1) AS capped)
     AS counted ON true
JOIN LATERAL (SELECT work.*, work.author_id AS parent_key,
                     row_number() OVER (ORDER BY <C.order>, work.id) AS position
              FROM work WHERE work.author_id = parents.key AND <C.where>
              ORDER BY <C.order>, work.id LIMIT :C.limit + 1) AS children ON true
ORDER BY children.parent_key, children.position
```

Elsewhere — SQLite, the engines without `LATERAL` — the portable form, a window:

```sql
SELECT related.*, parent_key, total FROM (
  SELECT work.*,
         work.author_id                                          AS parent_key,
         row_number() OVER (PARTITION BY work.author_id ORDER BY <C.order>, work.id) AS position,
         count(*)     OVER (PARTITION BY work.author_id)          AS total
  FROM work
  WHERE work.author_id IN (:P) AND <C.where>
) WHERE position <= :C.limit + 1
ORDER BY parent_key, position
```

One statement either way, with the same answer. Each parent gets its rows in the node's order, at
most `limit` — the row past it is read only to know there is more, and a cursor is minted from
the last row kept —, and the count its node asked for (`C.count`):

| `C.count` | The count per parent |
|---|---|
| `capped`, the default | exact up to `RELATION_COUNT_CAP` (1 000), past it a floor: `Count(1000, exact=False)`, «at least 1000» |
| `exact` | every row counted |
| `none` | none; the cursor alone says there is more |

A parent whose rows did not fill the page is counted for free, exactly. Paging on inside one
parent is an ordinary `search` on `Work` with `author_id = X` and that cursor.

Why two forms: a window partitioned by the parent numbers and counts every row of each parent
before the outer `WHERE` keeps 40 of them — PostgreSQL's own window optimisation does not stop
early under `PARTITION BY` — so a parent with a million children cost a million rows for a page of
40. The `LATERAL` join reads `limit + 1` rows per parent off an index on `(author_id, <order>)`
and stops, and the capped count stops one past the cap.

**Foreign key, to-one** (`Work.author`, key on this table): the distinct keys the parents hold,
`SELECT author.* WHERE id IN (:keys) AND <C.where>`, matched back by identity.

**Many-to-many**: the same two forms, joined through the table in between, matched — or
partitioned — by its key pointing at the parent.

**List of ids**: `SELECT related WHERE id IN (union of every parent's list) AND <C.where>`, ordered
by `C.order`; the cut per parent is in memory, because the related table has no column pointing
back.

**Bus, or function**: the *reflected criteria* is built, `C` plus `identified_by IN (:P)` (or the
related identity in the keys the parents hold, for a to-one), with `pagination.limit =
C.limit × len(P)` and `C.specification` passed through; one call; the records come back typed
as the other side's aggregate and are cut per parent in memory. The count per parent is exact
unless the other side filled the page it was asked for, in which case it says `exact = False`.
The other side is an ordinary `search`, so its own nested specification is resolved there.

**Whole** (a Feature touching the relation inside a unit of work): the same, with no page and no
count asked — every row, said in the log past `WHOLE_WARNING` ([§7](#7-lazy-inside-a-unit-of-work-refused-outside)).

## 6. What it costs

| Read | Statements or calls |
|---|---|
| a page | 1, plus 1 for the count unless the page came back short |
| a page with `k` named relations | `+ k` |
| a page with a relation and, inside it, another | `+ 2` |
| a page with a relation from another context | `+ 1` statement on this side, `+ 1` command on the other |
| the same at 500 rows | the same numbers |

| One parent with 1 000 000 children, a page of 40 | median |
|---|---|
| `LATERAL`, capped count (the default) | 6.9 ms |
| `LATERAL`, exact count | 29.2 ms |
| `LATERAL`, no count | 3.6 ms |
| the window, either count | 357–377 ms |

Measured 2026-11-06 through `repository.search` with the relation named, PostgreSQL 17 in Docker
on a laptop, an index on `(customer_id, number, id)`, median of seven runs after a warm-up.

The tests count statements, not shapes. A change that passes every functional test and adds a
statement per row fails `test_nesting_goes_as_deep_as_asked_with_one_statement_per_node`.

Where the chain does **not** compose, and is not meant to:

- **Filtering or ordering the parent by the child.** "Datasets whose latest run failed" is not a
  relation to resolve; it is a value that must live in the dataset's own table, maintained by
  an event (`Line.entry_state` in the ledger is the pattern). A resolver answers a detail view; it
  never answers a listing filtered by the other side.
- **Exact counts and global order across a bus.** Only per parent, and only as exact as the
  other side's page allows.
- **An exact count of a parent with a million children** reads a million index entries
  whatever the query looks like; that is why the default stops at a thousand and says «at
  least». A total a screen shows on every row belongs in the parent's own table, kept by an
  event, like the filter above.
- **Per-parent limit through a bus** travels as `grouping` by `identified_by` with the node's
  page in the reflected criteria. A repository of ours reads `grouping` with a page as «`limit`
  rows for every key» and cuts it in SQL, so no parent starves another; its answer has no
  cursor and an exact count, which is how this side knows the partition was honoured and can
  say a parent with fewer rows than the limit is complete. A function that ignores `grouping`
  answers a whole page, `limit × parents` are asked for, and nothing is claimed exact unless
  the whole page came back short. What a partitioned page cannot say is the total per key
  beyond the limit: a parent with exactly `limit` rows is reported as «at least `limit`».

## 7. Lazy inside a unit of work, refused outside

```python
with self.repository.context() as repository:
    entry = repository.get(Entry, entry_id)
    entry.lines                       # resolves whole on first touch, through the same resolver
    entry.lines[0].account.name       # a second hop, lazily

page = self.repository.search(Entries)
page[0].lines                         # RelationNotResolved: name it in the specification
```

The rule is the same one that made the algorithm: no hidden N+1. Inside a unit of work a Feature
decides a rule and needs the whole relation; outside one, a loop over two hundred detached
records would be two hundred queries hidden in an attribute access. A page a client cut with the
specification that is still on a record gives way to the whole when touched inside a context; a
Feature never decides on a cut page.

**Whole, and said when it is large.** A relation read whole by touching it brings every row —
the framework reads what it is asked to. Past `WHOLE_WARNING` (ten thousand) the log says so,
naming the relation, how many it brought and the cheaper ways to ask: a page, a count, each one by
cursor (`repository.search` / `count` / `stream` with the relation's key).

```python
with self.repository.context() as repository:
    customer = repository.get(Customer, customer_id)
    customer.invoices        # 2 million of them: all in memory, and a warning in the log
```

A record built in memory holds what its constructor was given: `Author(name="x").books == []`.
A record the mapper loaded starts unresolved.

## 8. An aggregate is saved whole

A to-many tied by a foreign key is written by its root: `save(root)` writes the children with
it, in the same flush and through the same rules — hooks, `version`, stamping — with the root's
key on them.

```python
workspace = Workspace(code="sp-1", repositories=[CodeRepo(name="api"), CodeRepo(name="web")])
repository.save(workspace)              # the workspace, then both repositories with its key

with repository.context() as unit:
    workspace = unit.get_by(Workspace, code="sp-1")
    workspace.repositories = [CodeRepo(name="cli")]
    unit.save(workspace)                # cli inserted; api and web are orphans
```

**An orphan is a child that was read and is no longer held.** Assigning the relation says
«these are the children now», as Rails' `collection=` does — and inside a unit of work an
assignment over a relation nobody read reads it first, so what it replaced is known. What is
settled is exactly what that reading saw:

| The relation | `save(root)` |
|---|---|
| never read nor assigned | nothing; no statement for it |
| read (whole, a page, or filtered) | writes the children that are new or changed |
| assigned over a whole reading | writes them, and settles the ones that reading saw and the root no longer holds |
| assigned with no whole reading behind it — over a page, a filter, or outside `context()` | writes them, removes nothing, logs a warning |

**The version is the aggregate's.** When `save(root)` writes, adds or drops any part — a child,
a grandchild, an orphan — the stored root is updated too, its version checked and raised, even
if none of its own fields moved. Two requests editing different lines of one invoice do not both
commit: the second is a `StaleAggregate`, and an invariant over the lines holds. A root whose
parts did not move retains its version ([decision 28](decisions.md#28-the-version-is-the-aggregates)).

```python
with repository.context() as unit:
    workspace = unit.get_by(Workspace, code="sp-1")
    workspace.repositories[0].name = "api-v2"
    unit.save(workspace)                # the repository, and the workspace's version: 1 → 2
```

A child another transaction added after the reading is not one the reading saw, so it is never
an orphan; a child it moved to another root is not either. The list a constructor gave is the
root's whole set, and once written it is what the root read: `Customer(name="Ana")` saved with
no invoices never removes the invoices saved for her later.

**What a dropped child becomes is declared, and refused until it is.** A child the root no
longer holds — dropped by an assignment, or left behind by `remove(root)` — is deleted with
`orphans=Orphans.DELETE`, has its key set to `NULL` with `Orphans.DETACH`, and with nothing
declared (`Orphans.REFUSE`) the write is refused before any statement runs, naming the relation
and the two declarations. A delete nobody declared is the one outcome that cannot be taken back —
Ecto's `on_replace: :raise`, EF Core's `Restrict`. An archived child is never an orphan.

**Removing the root settles its parts as declared.** "A delete removes everything within the
aggregate boundary at once" (Evans) — once the relation says so: `remove(root)` reads every child
of the relations the root owns, by the foreign key whatever the relation's `scope` shows, and
deletes or detaches them as `orphans` declares; a relation that declares nothing refuses while it
has children. A reference to another aggregate is declared `owned=False` and left to its foreign
key, which refuses the delete while children point at the root — Vernon's rule makes that case
rare: an aggregate holds another by id, not as a collection.

```python
remove(customer)    # Customer.invoices declares nothing  →  ProgrammingError: removing it would
                    # leave 3 Invoice of Customer.invoices … orphans=Orphans.DELETE takes them along,
                    # Orphans.DETACH sets their key to NULL
```

```python
map_aggregates(registry, tables, relations={
    Customer: {"invoices": TableRelation.foreign_key(Invoice, identified_by="customer_id", owned=False)},
    Ledger: {"lines": TableRelation.foreign_key(Line, identified_by="ledger_id", orphans=Orphans.DELETE)},
    Workspace: {"notes": TableRelation.foreign_key(Note, identified_by="workspace_id", orphans=Orphans.DETACH)},
})
```

| Declaration | `save` writes children | a dropped child, a removed root |
|---|---|---|
| inferred, or `TableRelation.foreign_key(...)` | yes | **refused** while there are children |
| `orphans=Orphans.DELETE` | yes | the children are deleted (a cascade) |
| `orphans=Orphans.DETACH` | yes | their key is set to `NULL` — the column must be nullable |
| `owned=False` — a reference to another aggregate | no | left to the foreign key |

A to-one is never owned: a child does not own its parent, and `owned` is read only on a to-many.

A child that is unchanged is not written again; one that changed is written with its own
`version` checked, so a stale child is refused as a stale root is. **A child moves between roots
in one call** — `save([old_root, new_root])`: saved apart, the first save removes it as an
orphan and the second is refused, saying so.

**A many-to-many writes its pairs, never the other aggregate.** The labels a ticket carries are
an aggregate of their own; the ticket holds only which ones, and those pairs live in the table in
between. `save(ticket)` inserts the pairs it holds and the table lacks, and deletes the ones its
last whole reading saw and it no longer holds — the same rule as an orphan, so a pair another
writer linked after the reading, or one a filter hid, is never deleted. `remove(ticket)` deletes
its pairs first and leaves the labels. A label is never inserted nor updated from the ticket,
whatever its fields hold in memory.

```python
with repository.context() as unit:
    ticket = unit.get(Ticket, ticket_id)              # labels {urgent, billing}
    ticket.labels = [billing, network]
    unit.save(ticket)        # (ticket, urgent) deleted, (ticket, network) inserted; version 1 → 2
```

| The relation | `save(root)` writes the pairs |
|---|---|
| never read nor assigned | none |
| read whole, or assigned over a whole reading | inserts the new ones, deletes the ones the reading saw and it no longer holds |
| assigned blind, or read cut | inserts the new ones, deletes none, logs a warning when blind |

A form names the related records by id — `{"labels": ["0192…", "0193…"]}`, or mappings carrying
the id, as a form reads them back — and `EntityReads` reads them in one search before `apply_values`
puts them on the record: an id with no record is an `InvalidValue` pointing at it
(`#/values/labels/1`), and a mapping without one is refused — a reference never creates the
record it names.

The cascade lives in `orm/sqlalchemy/services/cascade.py`: the relations are the framework's own
descriptors, not SQLAlchemy relationships, so it is planned before the flush and handed to the
same write path. `MemoryRepository` stores the root as the object it is, children inside, so the
aggregate it hands back is the one it was given.

## 9. A to-many is an `EntityCollection`

A read already answers a to-many as an `EntityCollection` — its records, how many there are, the
cursor to go on —, and the wire sends it so. Declaring it so makes the domain say the truth, and
it is the same type a TypeScript client holds (`EntityCollection<T>` in @sincpro/client-framework):

```python
@dataclass
class Invoice(Entity):
    lines: EntityCollection[InvoiceLine] = field(default_factory=EntityCollection)

invoice.lines.count          # Count(value=3, exact=True)
invoice.lines.cursor         # None: all of them are here
invoice.lines.is_partial     # False
for line in invoice.lines: ...
```

It is immutable and edited the way a `str` or a `date` is — every edit answers a new collection:

```python
invoice.lines = invoice.lines.append(InvoiceLine(qty=2))
invoice.lines = invoice.lines.extend([one, another])
invoice.lines = invoice.lines.remove(line)              # by itself or by its identity
invoice.lines = invoice.lines.replace(replace(line, qty=5))   # the same identity, other values
repository.save(invoice)
```

- An edit keeps what the collection says: a page stays a page (its cursor, a floor stays a floor),
  an exact count moves with what was added or removed. `remove` and `replace` of a record that is
  not held are a `ValueError`, as `list.remove` is.
- A copy made with `dataclasses.replace` is saved as an update of the stored row it copies,
  never an insert — and refused as `StaleAggregate` when the row moved on since the copy's
  version was read.
- A list given to the field — `Invoice(lines=[l1, l2])`, an assignment, a form's values — is held
  as the whole collection it is.
- `list[InvoiceLine]` keeps working, read and saved the same: the framework is permissive about
  the annotation; `EntityCollection` is the one it recommends, because it carries the count and
  the cursor a list throws away.
- A response declares either: `invoices: EntityCollection[Invoice]` holds the page with its
  count and cursor, `invoices: list[Invoice]` its records. On the wire both travel the same.

**A part or a reference** is the relation's declaration, not its type: a to-many is a part of its
root — saved whole with it, its orphans settled — unless declared `owned=False`, a reference to
other aggregates the root reads and never writes. The definition says `reference: true` on such a
to-many, so a screen lists and searches it and never offers it as lines to edit.

## 10. Extending it

**A new place to bring a relation from** is a callable. Nothing in the core changes:

```python
def fetch_prices(keys: Sequence[str], criteria: Criteria) -> list[Price]:
    """`criteria.where` carries `product_id in keys` plus whatever the node asked; honour what
    you can, drop what you cannot. Return records with `product_id` on them."""
    response = http.post(PRICING_URL, json=criteria.model_dump(mode="json"))
    return [Price.model_validate(one) for one in response.json()["items"]]

relations={Product: {"prices": TableRelation.resolved_by(Price, identified_by="product_id", resolver=fetch_prices)}}
```

Return an `EntityCollection`, a `ResponsePaginatedQuery` or a plain sequence; return the other
side's `Meta` inside either of the first two and it travels as the relation's `definition`.

**Another bounded context** is a `BusResolver`, built for you by `Relation.bus`. The other side
writes nothing special: a Feature that takes a `Query` and answers with `ResponsePaginatedQuery.of`.

**A new database kind** (say, a polymorphic key, or a relation through two tables) is a new
member of `RelationKind`, a constructor on the adapter's `Relation` and a new branch in
`relation_resolver._resolve`, reusing `_partitioned` and `_grouped`. Preserve the invariant: one
statement for the whole page. A kind is never compared as text: `relation.kind is
RelationKind.MANY_TO_MANY`.

**Another store that manages relations** puts a `RelationAttribute` on the entity's class for
each one — the contract the vocabulary asks, every answer given without reading anything:

| Method | Who asks | What it answers |
|---|---|---|
| `joined_by(record)` | `apply_values`, building a line a form sent without its key | `("id", "sale_id")` when a save of `record` writes the relation, else `None` |
| `holds_whole(record)` | `recompute`, before a total over the lines | whether the relation was read whole or assigned over a whole reading |
| `held_in_part(record)` | the warning of a total a save skipped | `"cut"`, `"blind"` or `""` |
| `named_by_id()` | `EntityReads.referenced`, `states_of` | `True` for a many-to-many: a form names its records by id |

`relation_attribute(Entity, "lines")` finds it, `None` for a plain field. A store with none —
`MemoryRepository` — holds its relations as plain fields: whole, tied by nobody, named by nobody.

**A new transport for the same idea** in another backend reuses everything in
`ddd/entity/relations.py`: `Relation`, `Resolver`, `resolve_elsewhere`, `cut`, `limit_of`. Only the
window-function statement is SQLAlchemy's.

## 11. A worked example, all kinds at once

`tests/data_layer/orm/every_kind_models.py` declares one aggregate with every kind of field and
`tests/data_layer/orm/test_field_meta_unified.py` asks for all of it in one criteria: nine scalars filtered,
an embedded value cut inside, a `many2one` inferred, a `many2many` declared, a `one2many` from
another context through its bus, a `many2one` from a function. `dropped` is empty, the wire shows
each relation under its own name, and the definition mirrors the mask. Read that test as the
executable version of this page.
