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

Two axes are kept apart on purpose. **What the relation is**, its shape, lives in `Meta` and is
what a client reads. **How it is brought**, its mechanics, lives in the data mapper as a
`Relation` and is what the resolver follows. `Work.reviews` from another context is a `one2many`
identified by `book_id` exactly like a same-database one; only the mechanics differ.

An **embedded value** is not a relation. A dataclass or pydantic model stored in a JSON column has
no identity, no life of its own and nothing to fetch: it is a field with `kind = "embedded"` and
a `definition` describing its shape. The mask cuts inside it; nothing resolves it. The line
between the two is lifecycle, not "simple versus compound".

## 3. What a name is: the precedence

`describe()` and `map_aggregates` decide together, in this order, and nothing overrides anything
by surprise:

1. **A column with that name is a field.** Scalar, or embedded when its annotation is a value
   object. Never a relation, whatever the annotation says.
2. **A declaration wins over inference.** `map_aggregates(..., relations={Work: {"editor":
   Relation.foreign_key(Person, identified_by="editor_id")}})` is final for `editor`.
3. **A foreign key is inferred when it is unambiguous.** The annotation points at a mapped class
   and exactly one `ForeignKey` ties the two tables; with several, the column named
   `<attribute>_id` (to-one) or `<table>_id` (to-many) decides. Inference runs over the whole
   registry on every mapping call, so a class mapped later completes the relations of one mapped
   earlier.
4. **A pointer nothing identifies is published, not expandable.** Annotation to a mapped class,
   no foreign key, no declaration: it appears with `identified_by = None`; asking for it drops
   with `not_expandable`.
5. **Everything else is ignored.** A dataclass with no table and no declaration is not a
   relation and not a field.

Cardinality always comes from the annotation: `list[Run]` is many, `Run | None` is one. Only
`many_to_many` is a declared type, because a list cannot say there is a table in between.
`tests/orm/test_relation_precedence.py` pins each rule.

## 4. Declaring what the tables cannot say

```python
map_aggregates(registry, {Author: author_table, Tag: tag_table, Work: work_table},
    relations={Work: {
        "tags":      Relation.many_to_many(Tag, through=work_tag, this_key="work_id", related_key="tag_id"),
        "sources":   Relation.id_list(Work, identified_by="source_ids"),            # ids in a JSON column
        "reviews":   Relation.bus(Review, reviews_bus, CommandSearchReviews, identified_by="book_id"),
        "publisher": Relation.resolved_by(Publisher, identified_by="publisher_id", resolver=fetch_publishers),
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

**Foreign key, to-many** (`Author.works`, key on the related table):

```sql
SELECT related.*, parent_key, total FROM (
  SELECT work.*,
         work.author_id                                          AS parent_key,
         row_number() OVER (PARTITION BY work.author_id ORDER BY <C.order>, work.id) AS position,
         count(*)     OVER (PARTITION BY work.author_id)          AS total
  FROM work
  WHERE work.author_id IN (:P) AND <C.where>
) WHERE position <= :C.limit
ORDER BY parent_key, position
```

One statement. Each parent gets its rows in the node's order, at most `limit`, an exact `total`,
and a cursor minted from its last row when there is more. Paging on inside one parent is an
ordinary `search` on `Work` with `author_id = X` and that cursor.

**Foreign key, to-one** (`Work.author`, key on this table): the distinct keys the parents hold,
`SELECT author.* WHERE id IN (:keys) AND <C.where>`, matched back by identity.

**Many-to-many**: the same window, joined through the table in between, partitioned by its key
pointing at the parent.

**List of ids**: `SELECT related WHERE id IN (union of every parent's list) AND <C.where>`, ordered
by `C.order`; the cut per parent is in memory, because the related table has no column pointing
back.

**Bus, or function**: the *reflected criteria* is built, `C` plus `identified_by IN (:P)` (or the
related identity in the keys the parents hold, for a to-one), with `pagination.limit =
C.limit × len(P)` and `C.specification` passed through; one call; the records come back typed
as the other side's aggregate and are cut per parent in memory. The count per parent is exact
unless the other side filled the page it was asked for, in which case it says `exact = False`.
The other side is an ordinary `search`, so its own nested specification is resolved there.

**Whole** (a Feature touching the relation inside a unit of work): the same, with no limit.

## 6. What it costs

| Read | Statements or calls |
|---|---|
| a page | 1, plus 1 for the count unless the page came back short |
| a page with `k` named relations | `+ k` |
| a page with a relation and, inside it, another | `+ 2` |
| a page with a relation from another context | `+ 1` statement on this side, `+ 1` command on the other |
| the same at 500 rows | the same numbers |

The tests count statements, not shapes. A change that passes every functional test and adds a
statement per row fails `test_nesting_goes_as_deep_as_asked_with_one_statement_per_node`.

Where the chain does **not** compose, and is not meant to:

- **Filtering or ordering the parent by the child.** "Datasets whose latest run failed" is not a
  relation to resolve; it is a value that must live in the dataset's own table, kept current by
  an event (`Line.entry_state` in the ledger is the pattern). A resolver answers a detail view; it
  never answers a listing filtered by the other side.
- **Exact counts and global order across a bus.** Only per parent, and only as exact as the
  other side's page allows.
- **Per-parent limit through a bus** is approximated as `limit × parents`. The clean end is for
  the reflected criteria to carry the partition as a `grouping` by `identified_by`, which the
  vocabulary already has. Open, and noted in [decisions.md](decisions.md).

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

A record built in memory keeps what its constructor was given: `Author(name="x").books == []`.
A record the mapper loaded starts unresolved.

## 8. Extending it

**A new place to bring a relation from** is a callable. Nothing in the core changes:

```python
def fetch_prices(keys: Sequence[str], criteria: Criteria) -> list[Price]:
    """`criteria.where` carries `product_id in keys` plus whatever the node asked; honour what
    you can, drop what you cannot. Return records with `product_id` on them."""
    response = http.post(PRICING_URL, json=criteria.model_dump(mode="json"))
    return [Price.model_validate(one) for one in response.json()["items"]]

relations={Product: {"prices": Relation.resolved_by(Price, identified_by="product_id", resolver=fetch_prices)}}
```

Return an `EntityCollection`, a `ResponsePaginatedQuery` or a plain sequence; return the other
side's `Meta` inside either of the first two and it travels as the relation's `definition`.

**Another bounded context** is a `BusResolver`, built for you by `Relation.bus`. The other side
writes nothing special: a Feature that takes a `Query` and answers with `ResponsePaginatedQuery.of`.

**A new database kind** (say, a polymorphic key, or a relation through two tables) is a new
`kind` on the adapter's `Relation` and a new branch in `relation_resolver._resolve`, reusing
`_partitioned` and `_grouped`. Keep the invariant: one statement for the whole page.

**A new transport for the same idea** in another backend reuses everything in
`ddd/relations.py`: `Relation`, `Resolver`, `resolve_elsewhere`, `cut`, `limit_of`. Only the
window-function statement is SQLAlchemy's.

## 9. A worked example, all kinds at once

`tests/orm/every_kind_models.py` declares one aggregate with every kind of field and
`tests/orm/test_field_meta_unified.py` asks for all of it in one criteria: nine scalars filtered,
an embedded value cut inside, a `many2one` inferred, a `many2many` declared, a `one2many` from
another context through its bus, a `many2one` from a function. `dropped` is empty, the wire shows
each relation under its own name, and the definition mirrors the mask. Read that test as the
executable version of this page.
