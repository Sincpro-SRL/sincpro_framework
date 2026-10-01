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

`ddd/criteria/criteria.py`, `orm/sqlalchemy/entrypoint/repository.py`, `orm/sqlalchemy/services/sql_translator.py`.

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

`ddd/entity/model_meta.py`, `orm/sqlalchemy/services/model_introspection.py`.

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

`ddd/criteria/criteria.py` (`Specification`), `Meta.only`, `ResponsePaginatedQuery.of`.

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

`ddd/entity/model_meta.py`.

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

`orm/sqlalchemy/services/model_introspection.py`, `orm/sqlalchemy/services/data_mapper.py`
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

`ddd/entity/relations.py`, `orm/sqlalchemy/services/relation_resolver.py`.

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

`orm/sqlalchemy/services/data_mapper.py` (`RelatedAttribute`), `session.info[REPOSITORY]`.

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
`search`; one `save` and one `remove` that each take one aggregate or several; `archive` for the put-away that `remove` would
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

## 11e. Two conventions an aggregate opts into: `AuditedMixin` and `ArchivableMixin`

**Decision.** `AuditedMixin` adds `created_by` / `updated_by`, stamped by the adapter from the
`Database`'s actor. `ArchivableMixin` adds `archived_at`: `archive` stamps it, `remove` deletes, and a
reading leaves the archived out unless the criteria names the column.

**Why.** Both are written by hand in every project, and both are wrong in the same way when
they are: somebody forgets. The actor comes from a callable the provider hands the database,
usually `lambda: bus.current_context().get("user.id")`, so the adapter never learns what a bus is. What a
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

## 12. The repository is concrete; the abstract store is what it answers

**Decision.** Applications inject the concrete `Repository` as `self.repository`. An abstract class in
`ddd/repositories/repository.py` lists what a store answers: the writes (`save`, `remove`,
`archive`) and the readings a Feature actually makes (`get`, `search`, `count`, `browse`,
`fetch_all`, `stream`, `first`, `one`, `get_by`, `exists`, `pluck`, `distinct`, `measures`,
`group_by`, `group_by_levels`). Declaring only the first handful type-checked a `Repository`
annotation against six methods while the code around it called eighteen. `context()` is the unit of work; `session`, `flush`, `commit`, `savepoint`, `for_update`
are real only inside it.

**Why.** An interface with one implementation is a promise nobody tests; the concrete class has
more, and that is the point of having one. `save` and `remove` take the aggregate and never a
criteria: a generic write path skips the aggregate's rules. Optimistic concurrency is SQLAlchemy's
`version_id_col` on `Entity.version`, surfacing as `StaleAggregate`, proven across two processes.

**Rejected.** Repository names from the ORM world (`SearchEngine`, `unit_of_work`): the words in
the framework are the domain's. Update or delete by criteria was rejected here too, for skipping
the aggregate's rules; §22 builds it anyway, as a door named for what it skips.

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

`orm/sqlalchemy/infrastructure/observability.py`.

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
- A message broker as a third `Queue`; an async engine; a second persistence backend.
- **`save` of a thousand loaded aggregates is a thousand `UPDATE` statements**, because the
  optimistic lock reads the affected row count back per row. That stays the default — losing a
  write silently is worse than a slow job. The separate doors that say plainly they give up
  `StaleAggregate`, `upsert` and `update_all`, are §22.

## 19. Migrations: the project writes the steps, the framework orchestrates them

A bounded context may own several stores, and contexts share databases, so there are many
chains of migrations and nothing in any one tool orders them. `sincpro_framework.migrations`
merges every chain into one timeline — each chain in its own order, `requires` first, the oldest
UUIDv7 among the rest — reads where each store stands from the store itself, and moves the whole
system forward or back as one. Each (context × store) is one linear chain with its own version
table, never Alembic branches, whose downgrades across bases corrupt the version table.

The core is engine-agnostic and needs no database: `MigrationEngine` is one abstract class, and
Alembic — `sincpro_framework.orm.migrations`, behind the `[migrations]` extra — is the engine
shipped for SQL stores. Migrations run with the system down, so there are no locks, and a failed
step stops the run where it is: nothing reverts on its own, and a store without transactions is
recorded dirty until a human resolves it. The design and its evidence: PRD_05.

---

## 20. An aggregate is saved whole; an orphan is what was read and is no longer held

**Decision.** A to-many tied by a foreign key is written by its root: `save(root)` writes the
children with it, stamping the root's key on them. An assignment over a whole reading settles
the children that reading saw and the root no longer holds — inside a unit of work an assignment
over an unread relation reads it first, as Rails' `collection=` does. What a dropped child
becomes is declared on the relation — `Orphans.DELETE`, `Orphans.DETACH` — and refused until it is
(`Orphans.REFUSE`, the default): an assignment that drops a child and a `remove(root)` that would
leave one are both refused before any statement runs, naming the relation and the declarations.
`owned=False` makes a reference to another aggregate, never written nor removed by the root.

**Why.** A root that does not save its children is the surprise every project wrote a loop to
work around, and forgot the delete half of. Every narrowing is a failure that was reproduced:

- *Settle against every stored row* deleted a child another transaction added between the read
  and the assignment — a reading says what it saw, not what is there now.
- *Settle on any whole reading* deleted an invoice saved separately, because the customer still
  held the empty list its constructor gave.
- *Remove only what is declared owned* was the next step, after `remove(author)` deleted every
  `Work` of a model that held another aggregate as a collection. It made "part of the parent" mean
  one thing for `save` and another for `remove`. Measured in the first consumer (`sincpro_forge`):
  every one2many is a part — repositories, checks, steps, changes, decisions — and every link
  between aggregates is an id, as Vernon asks. The parts go with their root; the rare reference
  says `owned=False`.
- *Read the children of a remove through the relation's scope* left the ones the scope hid, and
  the foreign key refused the delete.

- *Settle by the key column* — delete under `NOT NULL`, set to NULL under a nullable key, EF
  Core's rule — still removed or detached what nobody asked for: `remove(customer)` took every
  invoice, and a nullable key set a whole history to NULL. A silent delete and a silent detach are
  the same mistake; both are declared now, and refused until they are (Ecto's `on_replace: :raise`,
  EF Core's `Restrict`). Writing children stays the default — it destroys nothing.

**How.** The relations are the framework's descriptors, not SQLAlchemy relationships, so
`orm/sqlalchemy/services/cascade.py` plans the writes before the flush and hands them to the same path
every aggregate takes. Each relation remembers how its value got there (`Held`: assigned,
blind, read whole, read cut) and the identities its last whole reading or write held (`READ`).
Orphans are read in one statement per relation for the whole batch — by those identities and the
root's key, so a child another writer moved is left alone.

**Rejected.** `relationship(cascade="all, delete-orphan")`: SQLAlchemy's lazy loading back and a
second source of truth for every relation.

## 21. A transaction is configured where it begins, and its failures are named

**Decision.** `context(isolation=, read_only=, timeout=, engine=)`. An isolation level the dialect
lacks is refused; `read_only` is the framework's refusal of every write plus Postgres' own; a
timeout it cannot honour is warned about. The driver's errors are translated by SQLSTATE, MySQL
error number, SQLite error name, then message (`engine_errors.py`) into five facts, three of them
new: `ConstraintViolation` (a foreign key, an empty value, a check), `TransactionConflict` (a
serialization failure, a deadlock) and `TimedOut` (a lock it was told not to wait for, a statement
past its timeout). `retrying` runs a unit of work again for `StaleAggregate` and
`TransactionConflict` by default — not for `TimedOut`, whose bound was the caller's choice — and is
refused inside a unit of work, whose transaction is the one that lost. SQLite runs every isolation
level as serializable, which honours any of them. `nowait` joins `for_update` and
`skip_locked`, and asking for both of those is refused, because SQLAlchemy renders it and the
database rejects it.

**Why.** Every `IntegrityError` was a `DuplicateAggregate`, so a missing foreign key read as a
race and a retry loop retried a write that could never succeed. A serialization failure reached
the Feature as a driver exception, so `serializable` was unusable without importing psycopg.
Three exceptions and not one per SQLSTATE: the caller does one of three different things — fix
the write, run it again, or take the bound it set as the answer.

What the commit writes is §25.

## 22. Writes past the aggregate are doors of their own

**Decision.** `upsert(records, on=, update=)` — answering `Upserted(written, skipped)` through
`RETURNING`, on Postgres and SQLite — and `update_all(target, criteria, values)` /
`remove_all(target, criteria)`, on the port and on both stores. They skip hooks (except
`before_save`/`after_save` on an upsert), the cascade and change tracking, and say so first. They
keep what later writes depend on: `version` is raised and `updated_at` stamped, the scope and the
archived apply as in a read.

**Why.** The cases they serve have no aggregate rule to skip — an idempotent import, closing
expired sessions, re-stamping a column — and without a door they dropped to `unit.session`,
skipping the rules *and* the scope, the version and the stamp. A write by criteria refuses a page
and refuses any condition the aggregate cannot answer: in a read a dropped condition widens what
is shown, in a write it widens what is changed. An upsert deduplicates its batch by key, because
Postgres refuses a statement that touches a row twice and SQLite hides it. MySQL's `ON DUPLICATE KEY` is left out: it matches
any unique key rather than the one named, cannot leave a conflicting row as it is, and its
affected-rows count says neither what was inserted nor what was updated — Rails and Django expose
neither a portable conflict target nor portable counts for the same reason.

## 23. What waits for the commit waits for this one

**Decision.** `unit.after_commit(fn)` and `unit.after_rollback(fn)`, only inside `context()`. A
stack of levels on the session: `savepoint()` opens one and closes it — handing what it held to
the level around it, or running its `after_rollback` and dropping its `after_commit` when it was
undone. The outermost level answers to SQLAlchemy's `after_commit` and the outermost
`after_soft_rollback`. A callback that raises is logged and the rest still run.

**Why.** `Database.after_commit` is global, for every session the process opens; a Feature that
must publish only if *its* write committed had no door, and §14's rule — never publish from
inside a commit — had no tool to keep it. SQLAlchemy reports a savepoint's rollback as a
rollback too, and reports the end of a transaction before saying how it ended, which is why the
levels are opened and closed by `savepoint()` rather than read off the events. Not raising mirrors
Django's robust mode: the commit is final, and an exception would tell the caller otherwise.

## 24. The adapter is layered, and the layers point one way

**Decision.** `orm/sqlalchemy/` is in the four layers the other components use. `entrypoint/`
holds `Repository` alone: the unit of work and the typed surface a Feature calls, composed of
`services/` — `Reading` and `Writing` over a shared `Store`, the data mapper, the translator, the
resolver, the cascade. `domain/` is the adapter's vocabulary with no I/O; `infrastructure/` is
`Database` and what every statement goes through. A layer imports only the ones below it, and a
test holds the rule. Module names stay the ones §16 chose; only their layer is new.

Inside `services/` the same two levels a project has: atomic services, one job each, as Features
are; and `services/workflows/` — `UnitOfWork`, `Reading`, `Writing` over a shared `Store` — which
orchestrate them, as an ApplicationService orchestrates Features. An atomic service never imports
a workflow, and a workflow imports no other workflow but `Store`; the relation resolver takes the
one function it needs, `prepare`, instead of the repository it used to call back into. The
entrypoint is a facade: `Repository(UnitOfWork, Reading, Writing)`, nothing of its own today. An
operation that spans workflows is orchestrated there, because the facade is the one place that
sees them all — never by one workflow calling another.

**Why.** Seventeen modules side by side, one of them nearly two thousand lines, said nothing about
which piece is the door and which is the machinery behind it. The direction of the imports was
already wrong before anyone looked: the services read the relations through the module that was
meant to be the surface. Laid out by layer, the tree says what each piece is, and the test says
it when someone forgets.

## 25. What the commit writes is what was saved

**Decision.** `context(writes=Writes.SAVED)`, the default: an aggregate changed inside the block
and never handed to `save` is put back when the block ends — and at each `commit()` — and named
in the log; the session does not autoflush, so a read after the change does not write it either.
`writes=Writes.CHANGED` writes what the block changed on what it loaded, as the session tracks it.

**Why.** Fowler's Unit of Work registers objects one of two ways: the caller registers what it
wants written, or the object registers itself when it changes. Vernon names the repositories that
go with each: a collection-oriented one has no `save`, a persistence-oriented one does — and this
one does. Writing what was never saved mixed the two, and wrote past the aggregate's hooks and
cascade with nobody told: the silent UPDATE of a "read-only" endpoint is the failure dirty
checking is known for. Doctrine's `DEFERRED_EXPLICIT`, Django, Rails and Ecto write only what was
asked. The first consumer always calls `save` and has code that changes loaded objects and saves
them later; a forgotten `save` is now a line in the log instead of a write that skipped its rules.
SQLAlchemy has no such mode, so the unit of work puts the unsaved back itself.

## 26. A query is strict about the language, tolerant about the model

**Decision.** `Criteria` and every part of it refuse a key the language does not have —
`{"limit": 10}` at the top, `{"field", "op", "value"}`, a node mixing `all` and `any`.
`Criteria.model_validate(data, context=TOLERANT)` reads leaving them out, for a client of another
version. A *field* the model does not have is still dropped and reported (§3): that is the schema
moving under a saved reading, not a typo.

**Why.** Read leniently, a typo does not fail, it answers wrong: a misspelt filter key is dropped
and more rows come back. RFC 9413 calls the cure virtuous intolerance; GraphQL refuses an unknown
argument by specification, JSON:API answers 400 to an unknown query parameter, Elasticsearch's
query DSL to an unknown key. Pydantic's default ignores extra keys because it serves payloads that
evolve, not query languages. Turned on, it found a test ordering by `"direction": "desc"` — a key
the language never had, silently ignored since it was written — and a workflow condition whose
`all` beside an `any` had never been read.

