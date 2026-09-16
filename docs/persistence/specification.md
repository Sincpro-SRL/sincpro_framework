# Specification: what to bring back of each record

One field on `Criteria`, `specification`, says what is wanted OF each record: which scalars,
which relations, and how to bring each relation. It is the request contract for every read,
at the first level and at any depth below it.

```python
class Specification(RootModel[dict[str, Criteria]]): ...

class Criteria(DataTransferObject):
    where, order, pagination, grouping, count, meta      # as before
    specification: Specification | None = None           # field name → how to bring it
```

Every key is a field or a relation of the aggregate; every value is a `Criteria`, the same one
as outside. A scalar's is `{}`. A relation's carries `where`, `order`, `pagination` and its own
`specification`, so the vocabulary inside a node is the vocabulary outside it, recursively.

`None` and `{}` are not the same thing. `None` is «nothing asked»: every scalar, no relation.
`{}` is «asked, and nothing survived»: the identity alone.

## The rules

| # | Rule |
|---|---|
| 1 | Without a specification: every scalar, including every key such as `dataset_id`, and no relation resolved. |
| 2 | Naming a node brings all of its scalars and none of its relations. `"plan": {}` is the whole plan. |
| 3 | Naming its children cuts it to those children. |
| 4 | A relation is never expanded without being named, at any level. |
| 5 | The identity always travels, at every level, asked or not. |
| 6 | What is asked is a union: order does not matter, repeats collapse, a bare node wins over its own children. |
| 7 | What cannot be answered is dropped and reported, never a 400: `unknown_field`, `not_expandable`. |

Rule 1 said another way: with `dataset_id: str` and `dataset: Dataset` on an aggregate, a read
that asks nothing returns `dataset_id` and not `dataset`. `Meta.fields["dataset"]`, of type `many2one`, tells the
client the relation exists and what identifies it, so it can ask for it or filter by the key
without expanding anything.

**Two criterias merge with two laws.** `where` accumulates with AND, because both restrictions
hold. `specification` intersects, because a mask can only take away. That is what makes it safe
as a permission: cutting twice never shows more than cutting once, and two masks with nothing
in common leave the identity alone, never everything.

**No ceilings, only defaults.** A relation node without a `pagination` gets the default page,
40 per parent for a to-many. A client that writes `limit: 10000000` gets ten million. This is a
framework; the provider that wants a ceiling merges a criteria of its own on the way in.

## What the repository does with it

Today, the mask over scalars, end to end:

- `Meta.accept_specification` keeps what the model has, as a column or as a relation, and
  reports the rest in `dropped`. The repository does this beside the filter, so a page carries
  both kinds of dropped in the one list.
- `Meta.only(specification)` cuts the definition by the same mask, identity included. A client
  that asked for three fields sees three in the records and three in its filter builder.
- `ResponsePaginatedQuery.of` applies the mask where the answer is serialised and nowhere
  earlier: on the wire each record shows the named scalars plus the identity; inside the
  process the records stay the typed aggregates they are.

Resolution of a named relation. The vocabulary, `Relation`, `Resolver` and `BusResolver`, lives in
`sincpro_framework.ddd.relations` with no database in it; the kinds only a database has and the
SQL partitioning live in `orm/sqlalchemy/relation_resolver.py`:

- **One declaration, in the data mapper, beside the table.** `map_aggregates(..., relations=
  {Dataset: {"runs": Relation.foreign_key(Run, identified_by="dataset_id")}})`. The annotation on the
  aggregate gives the cardinality (`runs: list[Run]` to-many, `dataset: Dataset | None` to-one);
  the declaration gives the related aggregate, what identifies the relation and how to resolve it. `describe()` publishes all of it
  in `Meta.fields` as a field of type `many2one`, `one2many` or `many2many`, with `relation`,
  `identified_by` and, once expanded, `definition`: one map, the shape of Odoo's `fields_get`,
  and `Meta.relations` is only the view over the relational ones. An embedded value object in a
  JSON column is a field of type `embedded` whose `definition` is its shape; the specification
  cuts inside it the way it cuts a relation, without a page because it travels with the row.
  Foreign keys are read off the tables, so `many2one` and `one2many` need no declaration; only
  the table in between and what lives elsewhere are declared.
- **Four kinds, one shape.** `Relation.foreign_key` in the same database, with `Relation.many_to_many` with the table in between for a many-to-many; `Relation.id_list` for a list of ids held in a JSON column;
  `Relation.bus(Related, bus, Command, identified_by=…)` for another bounded context, whose command
  carries the reflected criteria and answers a paged response; `Relation.resolved_by(Related,
  identified_by=…, resolver=fn)` for HTTP, raw SQL, a serialised dictionary or anything, `fn(keys, criteria)`. The Feature that reads
  does not change between them.
- **Once per node, never per row.** Same-database kinds run one statement over the target with
  the parents' keys, partitioned by parent with `row_number() OVER (PARTITION BY key ORDER BY …)`
  so the node's `order` and per-parent `limit` hold in SQL, `count(*) OVER` giving each parent
  its exact count. Bus and function kinds make one call with the parents' keys and are cut per
  parent in memory. A nested specification recurses over all the children of a node at once.
- **What lands on the record.** A to-many becomes an `EntityCollection` on the parent's
  attribute, with `count` and a cursor: paging on inside a relation is an ordinary `search` on
  the target with the parent's key as filter and that cursor. A to-one becomes the record or
  `None`. The answer writes them under the node's name.
- **Lazy inside a unit of work, refused outside.** Inside `context()` a Feature navigates
  `dataset.producer.plan.owner` and `entry.lines` on first touch, whole, any depth, all three
  cardinalities, through the same resolvers. Outside a context a relation nobody named raises
  `RelationNotResolved`, because a loop over two hundred rows would be two hundred queries
  hidden in an attribute access. A record built in memory keeps what its constructor was given.
- **Defaults, not ceilings.** A to-many node without a page brings 40 per parent; a node that
  says a limit gets that limit.

## The request and the answer

```json
{"where": {"field": "name", "operator": "like", "value": "ventas"},
 "pagination": {"limit": 20},
 "specification": {
   "name": {},
   "producer": {"specification": {"stage": {}}},
   "sources": {"where": {"field": "row_count", "operator": ">", "value": 0},
               "order": [{"field": "registered_at", "descending": true}],
               "pagination": {"limit": 5},
               "specification": {"name": {}}}}}
```

Scalars come flat; a to-one as an object or `null`; a to-many as `{"items", "count", "cursor"}`.
Keys are the node names as asked, never dotted paths. The definition mirrors the mask, and for
every expanded relation carries the target's definition cut the same way, with what identifies it.
