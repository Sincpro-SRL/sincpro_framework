# The persistence layer: what it is, and why each piece is the way it is

This page is the reasoning. The other design pages say *what* the layer does
([persistence.md](reference.md), [specification.md](specification.md), [events.md](../events/README.md),
[real-world-suite.md](testing.md)); this one says *why*, decision by decision, with the
alternative that was rejected and where the decision lives in the code. Read it before changing
any of them: most of the shape below is the residue of a wrong turn already taken once.

The layer exists so that every Sincpro service asks a database the same way, from a screen, from
another service, from a test, and so that a bounded context can grow from one SQLite file to
Postgres, to several databases, to another context's bus, without its Features noticing.

---

## 1. Two packages, one line between them

**Decision.** `sincpro_framework.ddd` is vocabulary with no database in it: `Entity`, `Criteria`,
`Specification`, `EntityCollection`, `Meta`, `Relation`, `DomainEvent`, the exceptions.
`sincpro_framework.orm` is the SQLAlchemy adapter, an optional extra. Nothing under `ddd/`
imports SQLAlchemy.

**Why.** Most services that install the framework never touch a database from it: they run a bus.
They must not pay for a driver, and a service that only *speaks* `Criteria`, a gateway, a
frontend adapter, must not need one either. `tests/orm/test_optional_extra.py` and
`tests/test_backward_compat.py` block `sqlalchemy` in a fresh interpreter and prove the line
holds, and prove that the bus a project already runs on is untouched by all of this.

**Rejected.** One package with SQLAlchemy as a hard dependency. Simpler, and it would have made
the vocabulary unusable exactly where it is most useful, at the edges.

---

## 2. `Criteria` is the boundary language; inside a Feature, SQLAlchemy is whole

**Decision.** Everything that crosses a boundary, an HTTP request, a command to another context, a
saved reading, a relation to resolve, is a `Criteria`: `where`, `order`, `pagination`,
`specification`, `grouping`, `count`, `meta`. Inside a Feature, `repository.context()` hands the
real session and the whole of SQLAlchemy.

**Why.** A boundary language has to be small, serialisable and the same on every side, or every
client reinvents a filter syntax. A Feature, on the other hand, has to write a join across four
tables when it needs one; hiding SQLAlchemy from it would only produce a worse SQLAlchemy.
`Criteria` is also **reflexive**: the answer carries `Meta`, which says what may be asked next, so
a client builds the next criteria from the previous answer without a schema of its own.

**Rejected.** A repository method per question (`find_by_owner`, `find_recent`, …), which is how
every project started and how every project ended with sixty methods and no pagination. And a
full query DSL, which is a second SQL nobody can type-check.

`ddd/criteria.py`, `orm/sqlalchemy/repository.py`, `orm/sqlalchemy/sql_translator.py`.

---

## 3. What cannot be answered is dropped and said, never a 400

**Decision.** A condition on a field the model does not have, an operator its type does not take,
a value that will not read, a specification node that does not exist: each is removed and
reported in `dropped` with a reason. The request still runs.

**Why.** A criteria lives in URLs and saved readings, and those outlive the schema they were written
against. A renamed column must not break every shared link. A dropped filter always *widens*
the result, never narrows it, so saying it out loud is enough for the client to act.

`Meta.accept`, `Meta.accept_specification`, `EntityCollection.dropped`.

---

## 4. The definition travels with the answer

**Decision.** Every paged answer carries `Meta`, the definition of what was read: fields with type,
operators, choices, labels in every language, relations with what identifies them. Cut by the
same mask as the records.

**Why.** A client that receives records but not their definition cannot build a filter form, a
column menu or the next request without a second call and a schema to keep in step. The
definition is also what makes a mask *one* mask: three fields in the payload, three in the
definition, so the interface cannot offer what it was not given.

**Rejected.** A separate `describe` endpoint, tried first to save a kilobyte per page. It cost more
in surprise than it saved in bytes.

`ddd/model_meta.py`, `orm/sqlalchemy/model_introspection.py`.

---

## 5. `Specification`: field → criteria, recursive

**Decision.** `Criteria.specification` is a mapping from a name to a `Criteria`. A scalar's is
`{}`. A relation's carries its own `where`, `order`, `pagination` and `specification`. It is the
one way to say what to bring back of each record, at the first level and at any depth.

**Why.** Because it composes: `q(g(f(x)))`. Each node is the same type as its parent, so the
same vocabulary reaches every level and the framework resolves one node after another without
learning anything new. Odoo arrived at the identical shape (`web_read` takes a `specification`
dict); OData's `$expand($filter;$orderby;$top)` and GraphQL's nested selections say the same
thing with more syntax. It also makes a mask safe as a permission: two specifications
*intersect*, so cutting twice never shows more than cutting once.

**Rejected.** A `resolve` field beside the mask, proposed once during this work. It split one idea
into two names and reinvented half of what already existed. A dotted-path list (`fields=a.b.c`)
as the canonical form: fine for URLs, and it expands to the mapping, but it cannot carry a
per-relation `where`.

**The rules that took arguing.** Without a specification, every scalar and every key, no relation:
a `dataset_id` is a scalar, so the client already has it. Naming a bare relation is the whole
record. The identity always travels; a record whose id did not come cannot be opened, refreshed
or cached. `None` and `{}` are opposite answers: "asked nothing" and "asked, nothing survived".

`ddd/criteria.py` (`Specification`), `Meta.only`, `ResponsePaginatedQuery.of`.

---

## 6. One map of fields, the shape of Odoo's `fields_get`

**Decision.** `Meta.fields` holds scalars, embedded values and relations alike. `FieldMeta.kind`
says which in one word, `scalar | embedded | relation`; `FieldMeta.type` says which precisely:
`text`, `integer`, …, `embedded`, `many2one`, `one2many`, `many2many`. A relational field carries
`relation` (the aggregate on the other side), `identified_by` (the column that ties them) and,
once expanded, `definition`. `Meta.relations` is a view over the relational ones.

**Why.** A client asks one question of a name: what is it. Two maps forced two lookups for one
menu. Odoo has run one namespace with a dozen types for twenty years; the team already thinks in
`many2one` and `one2many`, and those words say cardinality and the side of the key at once.

**Rejected.** A separate `RelationMeta`. It existed for a day. It doubled the lookups, and the
separation it drew, "relations are different", is already drawn by `type`.

`ddd/model_meta.py`.

---

## 7. Types are read off Python and the tables, not declared

**Decision.** `describe()` reads the field type from the annotation, nullability from the column,
choices from the enum, labels from `translations()`. A column whose annotation is a dataclass or a
pydantic model with no table is `embedded`, with the shape of that class as its `definition`.
`many2one` and `one2many` are inferred from the `ForeignKey` constraints already in the tables.
Only what nothing can say is declared beside the tables: the table in between a many-to-many,
and what lives in another context or behind a function.

**Why.** Pure Python is the declaration the team already writes. A second place to say "this is a
relation" drifts from the first; the tables cannot drift from themselves. Precedence is fixed so
nothing is overwritten by surprise: a column wins over a name, a declaration wins over inference,
inference only when it is unambiguous, and an ambiguous or unresolvable pointer is published but
`not_expandable`.

`orm/sqlalchemy/model_introspection.py`, `orm/sqlalchemy/data_mapper.py`
(`_inferred_foreign_keys`), `tests/orm/test_relation_precedence.py`.

---

## 8. Relation is what composes; embedded is what is described

**Decision.** A relation points at an aggregate with a life of its own: it is brought by a
resolver, once per node for a whole page, cut and paged per parent. An embedded value object
travels inside the row: it is described, its shape is published, the mask cuts inside it, and it
is never resolved or paged.

**Why.** The line that separates them cleanly is lifecycle, not "simple versus compound". Anything
that has to be *fetched* needs `identified_by`, a resolver and a page; anything that arrives
with the row needs none of that. Precedents agree: GraphQL object types without resolvers,
MongoDB embedded documents, SQLAlchemy `composite()`.

`FieldType.EMBEDDED`, `describe_shape`, `query._shaped`.

---

## 9. Four ways to bring a relation, one shape, never per row

**Decision.** `Relation.foreign_key` and `Relation.many_to_many` in the same database, resolved in
one statement over a window (`row_number() OVER (PARTITION BY key ORDER BY …)`) so order and
per-parent limit hold in SQL and each parent gets an exact count. `Relation.id_list` for ids held
in a JSON column. `Relation.bus` for another bounded context, its command carrying the reflected
criteria. `Relation.resolved_by` for a function: HTTP, raw SQL, a serialised dictionary, anything.
The Feature that reads does not change between them.

**Why.** The invariant is cost: a page with three relation nodes is four calls with one row or with
five hundred. That is what DataLoader sells and what this has by construction. The `Resolver`
protocol, `(keys, criteria) -> records`, is the single extension point: a new transport is a
new resolver and the core does not change. Bus and function kinds are cut per parent in memory
after one call, and their count is honest: `exact=False` when the other side filled the page it
was asked for.

**Rejected.** SQLAlchemy `relationship()` loaders for the specification path. They cannot filter,
order or limit *per parent* from a criteria, and they tie the mechanism to one database.
`relationship()` remains the right tool for a Feature's own hand-written joins.

`ddd/relations.py`, `orm/sqlalchemy/relation_resolver.py`.

---

## 10. Lazy inside a unit of work, refused outside

**Decision.** Inside `repository.context()` a Feature navigates `dataset.producer.plan.owner` or
`entry.lines` on first touch, whole, any depth. Outside a context, touching a relation nobody
named raises `RelationNotResolved`. A page cut for a client that is still on the record gives way
to the whole when touched inside a context.

**Why.** A Feature deciding a rule needs the whole relation, exactly as Odoo's `record.line_ids`
gives it. A listing on a server needs the opposite: a loop over two hundred rows must not become
two hundred queries hidden in an attribute access. Raising puts the cost where a reviewer sees it,
in the specification, and never in a silent N+1.

`orm/sqlalchemy/data_mapper.py` (`RelatedAttribute`), `session.info[REPOSITORY]`.

---

## 11. Defaults, never ceilings

**Decision.** A relation node without a page brings 40 per parent. A node that says `limit: 80`
brings 80; one that says ten million brings ten million. The capped count stops at ten thousand
and *says so* with `exact=False`; `count=exact` pays for the real number.

**Why.** This is a framework. A ceiling the provider cannot lift is a bug report waiting; a default
the provider can change is a convenience. What was said is read off `model_fields_set`, so an
explicit `limit: 50` stays 50 although 50 is also the default.

---

## 11b. `grouping` with a page is a page per group, and a group answers ids

**Decision.** `grouping` together with an explicit `pagination` means «a page per group». On
`group_by_levels`, every group of the deepest level carries the identities of its first `limit`
rows in the criteria's order, an exact `count` and a `cursor`; opening a group is
`browse(Aggregate, bucket.ids)`, going on inside it is `search(bucket.criteria.resuming_from(
bucket.cursor))`. On `search`, the same two mean `limit` rows for every group in one statement,
which is what the reflected criteria of a relation asks the other side for.

**Why.** Groups are consumed by screens that list thousands of them and open a few: ids are light,
stable references, the way Odoo's `search` answers before `browse` does, and the specification
never runs for rows nobody opens. One window statement per level gives every group its page,
its count and its cursor, so the cost does not grow with the number of groups. The same
statement closes the gap of the bus: the partition travels as vocabulary the other side already
has instead of a private protocol.

**Rejected.** Records inside the groups: heavy and redundant with `browse`. A separate API for
"the first N of each": one more thing to learn where two existing words compose.

`Bucket.ids`, `Bucket.cursor`, `Repository._ids_per_group`, `Repository._partitioned_page`,
`ddd.relations._reflected`.

## 11c. The short readings, the batch and the retry are on the repository

**Decision.** `exists`, `first`, `one`, `get_by`, `pluck`, `distinct` and `export` beside
`search`; `save_all` and `remove_all` beside `save`; `purge` for the delete `remove` would
otherwise turn into an archive; `retrying(work)` for a unit of work that lost a race.

**Why.** Every one of them was already being written, once per project, as a wrapper around
`search` or a loop around `save` — and each wrapper is where a page slips in by accident, where
a batch becomes a thousand round trips, where a retry forgets to back off. The rule that keeps
them honest is the one the layer already had: what answers over a page says so, and what answers
over the whole result set says so. `pluck` and `distinct` are in the second group, with `count`
and `measures`.

`retrying` takes a callable and not a block, because a `with` cannot run its body twice.
`one` fetches two rows and no more: it never loads a set to discover it was not one.

**Rejected.** A `limit` argument on the short readings: the criteria already has one. Update or
delete by criteria, again.

## 11d. A repository can be narrowed, and a narrowed one never reads wide

**Decision.** `repository.narrowed(criteria)` answers the same object with that criteria merged
into every reading and checked against every write, with the evaluator. An aggregate that
cannot express the scope is refused outright.

**Why.** A tenant, a branch and a permission are all the same shape, and the laws they need are
the ones `merged_with` already has: `where` accumulates with AND, `specification` intersects.
Handing a Feature a repository that is already narrowed is stronger than asking it to remember
a filter, and it composes: narrowing again narrows further.

The refusal is the point. A scope whose field the aggregate does not have would be *dropped* by
the ordinary rule, and a dropped filter widens — which for a permission means handing over the
table. So this one place raises instead.

## 11e. Two conventions an aggregate opts into: `Audited` and `Archivable`

**Decision.** `Audited` adds `created_by` / `updated_by`, stamped by the adapter from the
`Database`'s actor. `Archivable` adds `archived_at`: `remove` archives, `purge` deletes, and a
reading leaves the archived out unless the criteria names the column.

**Why.** Both are written by hand in every project, and both are wrong in the same way when
they are: somebody forgets. The actor comes from a callable the provider hands the database,
usually `lambda: bus.context.get("user.id")`, so the adapter never learns what a bus is. What a
business calls deleting is almost always archiving, because other records point at the row;
Odoo spells it `active`, and a column that says *when* it was put away is worth more than a flag.

**Rejected.** Making either one automatic for every `Entity`. A convention that cannot be
declined is a tax.

## 11f. A repository with no database, for testing a Feature

**Decision.** `MemoryRepository(*records)` answers the vocabulary over records in memory: the
reads, the short readings, the folds, the groups, and writes with the version check and the
archive rule. Relations, units of work and date grains are the adapter's and say so.

**Why.** A Feature that reads is most of what a bounded context is, and testing it against a
double that somebody wrote by hand tests the double. This one filters with `matches`, the same
evaluator the SQL translator is specified against, so a Feature that passes here and fails
against the engine has found a bug in the engine.

## 12. The repository is concrete; the protocol is minimal

**Decision.** Applications inject the concrete `Repository` as `self.repository`. A `Protocol` in
`ddd/repository.py` lists the minimum (`get`, `search`, `count`, `save`, `remove`) for a test
double. `context()` is the unit of work; `session`, `flush`, `commit`, `savepoint`, `for_update`
are real only inside it.

**Why.** An interface with one implementation is a promise nobody tests; the concrete class has
more, and that is the point of having one. `save` and `remove` take the aggregate and never a
criteria: a generic write path skips the aggregate's rules. Optimistic concurrency is SQLAlchemy's
`version_id_col` on `Entity.version`, surfacing as `StaleAggregate`, proven across two processes.

**Rejected.** Update or delete by criteria. Repository names from the ORM world (`SearchEngine`,
`unit_of_work`): the words in the framework are the domain's.

---

## 13. `Entity` is a plain dataclass; ids are UUID v7

**Decision.** `Entity` gives `id`, `created_at`, `updated_at`, `version` as keyword-only defaults
and one class method, `translations()`, a TypedDict of `dict[str, str]` texts with a `default`
key and no assumed language. Ids are UUID v7, monotonic within a process.

**Why.** Time-ordered ids make "order by id" a time order and index inserts append-only. The
fallback for Python 3.12 and 3.13 uses a counter within the millisecond, so ids minted in order
sort in order, which the event envelope relies on. Translations live on the class because that is
where the words are known; `Meta` carries them and nothing merges or guesses a language.

---

## 14. Events: recorded by the aggregate, published by a Feature, stored by nobody

**Decision.** `Entity.record()` keeps events in memory; `pull_events()` is explicit. A `Publisher`
has the bus's signature: `publish(event)` or `publish(event, ResponseDTO)`. A `Subscriber` is a
list of `UseFramework` instances; a bus subscribes by registering a Feature for the event class,
`@bus.feature([Command, Event])`, nothing more. `SyncQueue` runs in the call; `BackgroundQueue`
in a spawned process, and survives a subscriber that raises.

**Why.** An event is a DTO and the bus already routes DTOs; a second registry would drift from the
first. Storing events, an outbox, session hooks, were built once and removed: durability is the
project's decision through its own unit of work, and a hook that publishes inside a commit
publishes what a rollback then undoes.

`ddd/events.py`, `events/`.

---

## 15. The adapter observes itself, through what the framework already has

**Decision.** `Database` logs every statement at DEBUG, with duration and row count, through the
logger it is given or its own `sincpro_framework.sql`; puts a span per statement on the trace
only when the process is collecting; reports every failure to GlitchTip. No parameter value ever
reaches a log, a span or a report.

**Why.** Debugging a Feature down to its statements next to its own log lines is the daily need; a
separate `instrument()` call was one more thing to forget. Values are kept out because a
repository that refuses to log dataset contents cannot let a trace do it under another name.

`orm/sqlalchemy/observability.py`.

---

## 16. Declarative module names

`database.py`, `repository.py`, `sql_translator.py`, `data_mapper.py`, `model_introspection.py`,
`custom_fields.py`, `relation_resolver.py`, `observability.py`. A module is named after the
thing it is, not after a generic noun; `types.py` and `reader.py` said nothing. `data_mapper` is
named after the pattern it implements and against the one it avoids, Active Record.

---

## 17. How this is tested, and why that way

- **Unit suites per layer**, `tests/ddd`, `tests/orm`, `tests/events`: fixtures, no `conftest`
  imports, models in their own module, one in-memory database per test.
- **The N+1 is pinned by counting statements**, not by shapes: every promise about cost reduces
  to a number, and a number is only a guarantee if a test asserts it.
- **A model with every kind of field** (`tests/orm/every_kind_models.py`) and **one criteria that
  asks for everything**, written before the code: the specification of the unified definition.
- **Precedence and edges** (`tests/orm/test_relation_precedence.py`): the cases where a name could
  be read two ways, empty values, a pydantic value object deserialised from JSON.
- **The real-world suite** (`tests/realworld`): an accounting ledger, three bounded contexts, the
  event chain, two processes on one row, the async door, at 2 000 or 25 000 entries, timed.
- **Backward compatibility** in a fresh interpreter with warnings as errors and SQLAlchemy blocked.

---

## 18. What is deliberately still open

- Filtering or ordering a parent *by* a relation does not compose; it needs the value in the
  parent's own table, kept current by an event (`Line.entry_state` in the ledger is the pattern).
- A page of groups applies to the first level; a deeper level answers for the groups that
  survived it. Ordering groups by an aggregate has no stable keyset, so the page is an offset.
- A message broker as a third `Queue`; an async engine; a second persistence backend, which is
  what would earn a `Protocol` in front of the adapter.
