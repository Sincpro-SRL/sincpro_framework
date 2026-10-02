# The persistence landscape — where the framework stands, and what comes next

A research synthesis (October 2026) of four fronts, each read against what the framework already
has — [use-cases.md](use-cases.md), the [manifesto](manifesto.md), the code. It is the reasoning
behind the roadmap at the end; the per-use-case status lives in the matrix.

| Front | The question |
|---|---|
| [1. Many stores](#1-many-stores) | What one repository can honestly promise over relational, document, wide-column and key-value stores |
| [2. Events](#2-events-event-sourcing-and-distribution) | Event sourcing, CQRS, outbox, sagas — and what goes wrong migrating to them |
| [3. Volume](#3-volume-millions-of-rows-and-data-engineering) | An OLTP domain model meeting millions of rows and analytics |
| [4. Records](#4-records-collections-and-enterprise-concerns) | How entities and collections are handed to developers, and the concerns every record system meets |
| [5. Styles](#5-styles-and-the-shape-of-the-repository) | Active Record, Data Mapper, the database context, a repository per aggregate — and which shape ours takes |

The model all four start from: everything is an **entity** — an aggregate, an MVC model, a DDD
entity, a composed value object, a NoSQL document — and a **collection** of entities. The framework
keeps the two apart on purpose: a `Criteria` is the question, an `EntityCollection` the answer.

---

## 1. Many stores

**What is portable, with the same meaning everywhere.** Read and write by full identity; an
optimistic write by version (Mongo's filter on `{_id, version}`, Dynamo's `ConditionExpression`,
Cosmos' `_etag`, Cassandra's `IF` at the price of a lightweight transaction); an opaque
continuation token (Dynamo's `LastEvaluatedKey`, Cassandra's paging state, Elasticsearch's
`search_after`); equality on a key or an indexed attribute. `save` raising `StaleAggregate` and a
keyset cursor are genuinely portable.

**What is inherently per store.** Joins (EF Core's Cosmos provider has no `Include`); aggregation
(Mongo has a pipeline, Dynamo none); the order a partition and sort key allow; consistency (Dynamo
secondary indexes are eventual only); transaction scope (Mongo needs a replica set and aborts at
60 s; Dynamo takes 100 actions, writes only; Cassandra one partition); offset paging (impossible on
Dynamo and Cassandra).

**How mature frameworks hold the line.** Jakarta Data standardises a subset and *requires* an
`UnsupportedOperationException` where a store cannot honour a word; Micronaut Data fails
compilation; Spring Data DynamoDB silently falls back to a full Scan (its issue tracker is the
cautionary tale); Hibernate OGM forced JPA onto NoSQL and was archived in 2018.

**What it means for us.**

- Our rule — refuse rather than ignore (manifesto 5, 10) — is Jakarta Data's, and right.
- **`for_update` / `skip_locked` / `nowait` fail question 1 for document stores**: they reached the
  port on the strength of SQL and memory alone. They should be a declared capability, refused by a
  store without row locks.
- Owned children are *natural* in a document store: the root is one document, `save` one atomic
  replace — and the aggregate's version moving with its parts comes for free.
- References carry over as they are: one batched `$in` / `BatchGetItem` per node, never per row.
- What a second store needs: **capabilities** — which operations it honours, now declared as types
  and `StoreCapabilities` (§5), to grow with isolation, transaction limits, offset paging, count
  cost — and **declared access patterns** (partition, sort, secondary indexes) with a planner that
  classifies each `Criteria` as key, range, index or scan: `explain` reports it, a scan warns, a
  mapping can refuse it.
- A decision to take: one port with capability refusals (Jakarta Data), or a narrower tier for
  `measures`/`group_by`/`distinct`/`update_all`/`remove_all`, which wide-column and key-value
  stores cannot answer.

## 2. Events, event sourcing and distribution

**How mature event stores work** (KurrentDB, Marten, Axon, Eventuous, Python `eventsourcing`): a
stream per aggregate appended with an *expected revision* — the concurrency control; one global
ordered log read with a checkpoint; projections inline (same transaction) or asynchronous (a
daemon), both rebuildable; snapshots for long streams; versioned events with upcasters.

**What companies hit migrating** — monolith to services, CRUD to events, sync to async:

1. **The dual write** — save, then publish — loses facts on a crash. The cure is the outbox, read by
   a polling relay or by CDC (Debezium's Outbox Event Router).
2. **Exactly once is a myth outside a broker's own loop**: effect-once is at-least-once plus an
   idempotent handler or an inbox.
3. **The Postgres outbox visibility gap**: ids commit out of order, so a reader checkpointing on
   "max id" skips late commits forever. The fix stores the transaction id (`xid8`) and reads below
   `pg_snapshot_xmin(pg_current_snapshot())`.
4. **Order exists per key only**; a parallel relay breaks it unless it partitions by key.
5. **The shared database** stalls a strangler-fig migration: each service owns its data and
   publishes facts.
6. **Event sourcing without a versioning strategy**, with streams too large and projections that
   cannot be rebuilt.
7. **GDPR on immutable facts**: crypto-shredding — a key per data subject, deleted on erasure.
8. **Long processes** need sagas: choreography, orchestration with compensation (MassTransit,
   NServiceBus routing slips) or durable execution (Temporal).

**Where we are.** We have the envelope (`correlation_id`, `causation_id`), `record`/`pull_events`,
the publisher and queues, FastStream brokers with an inbox, `EntityUpdated`, `after_commit` — and
the model the mature stores share (PRD_19, [decision 31](decisions.md#31-a-bounded-contexts-events-are-entities-in-one-table)):
a domain event as an entity in one table per bounded context, kept by the repository in the
transaction of the change; `DeliverableEventMixin` with the delivery state on the row, delivered
by an `EventRelay` with `FOR UPDATE SKIP LOCKED` and failures decided by a strategy — the outbox's
polling publisher, with no checkpoint and so no Postgres visibility gap; and `EventSourcedMixin`,
rebuilt from the same table, two writers kept apart by a unique `entity_version`. Replay and
fan-out to other systems are the broker's. Snapshots and sagas are not built.

## 3. Volume: millions of rows and data engineering

**The state of the art.**

- **Hydration is the dominant cost**, not the SQL: a row into an ORM instance into a domain object
  costs 10–50× a tuple. Mature stacks keep two read paths — aggregates for writes and invariants,
  *projections* (tuples, DTOs, Arrow) for everything else.
- **Bulk loads** above 10⁵ rows use Postgres `COPY` (psycopg 3) or ADBC ingest, at hundreds of
  thousands of rows per second; SQLAlchemy 2's "insertmanyvalues" batches the rest. One UPDATE per
  row is the price of optimistic locking.
- **Physical design**: declarative partitions (pruning only with the key in the WHERE), BRIN for
  append-only time, partial and covering indexes, GIN for JSONB and full text; TimescaleDB
  continuous aggregates refresh only the buckets that changed. `REFRESH MATERIALIZED VIEW
  CONCURRENTLY` is a full recompute; event-maintained projection tables are the usual alternative.
- **Read scaling** with replicas behind a read-only bind, and read-your-writes kept on the primary.
- **Analytics never goes through the ORM**: ADBC reads Postgres binary `COPY` straight into
  Arrow; DuckDB and Polars take Arrow without a copy; Ibis compiles one expression to twenty
  engines; Delta, Iceberg and DuckLake add ACID tables over Parquet.

**Where we are.** Keyset pagination, capped counts and every aggregation in SQL are solid. But
`export` and `data_analysis` hydrate every row before turning it into Arrow — the slowest path
there is; there is no projection read, no Arrow-native read, no streamed Parquet writer, no `COPY`
load, no view mapping, no replica routing, and `explain` counts statements but no guard warns at
run time.

## 4. Records, collections and enterprise concerns

**What to copy.** The query as an immutable value (SQLAlchemy `select()`, Ecto, our `Criteria`);
explicit loading (Ecto, Prisma, us); named scopes that compose; refusing in-memory evaluation where
it would be wrong (EF Core 3; our `is_partial` folds); global filters with a named bypass (EF Core
`HasQueryFilter`); batched loading per page (Odoo's prefetch, made explicit).

**What to avoid.** A query that runs because an attribute was read; a lazy object that runs on
`bool()` or `len()` (Django); `default_scope` (Rails); **one object that is both the question and
the answer** (Django, LINQ's `AsEnumerable` trap) — the source of the expensive silent mistakes.
`EntityCollection` stays an answer and never turns lazy; the convenience goes on the question side.

**The concerns every record system meets**, against what we have:

| Concern | Status |
|---|---|
| Optimistic lock, idempotency, soft delete, audit, change history, i18n text | ✅ |
| Tenant scope | ✅ `narrowed`, applied by hand |
| A concise query and named scopes | 🟡 `Criteria` is correct and verbose; `combined()` only |
| Gapless numbering per (company, series, year) — fiscal invoices | ✅ `DatabaseNumbering`, a counter row taken in the unit of work |
| Rules bound to the request (tenant, company) on every read, checked on every write (`check_company`) | ⬜ |
| Money (amount and currency, rounding per currency) | 🟡 a rule, no type |
| Stored derived fields that can be searched | 🟡 hooks compute them; nothing declares them |
| As-of reads, temporal tables | ⬜ |
| Attachments and blobs | ⬜ |
| GDPR erasure | ⬜ |

## 5. Styles, and the shape of the repository

**The styles in use.**

| Style | Who | The idea | Strong at | Weak at |
|---|---|---|---|---|
| Active Record | Rails, Django, Odoo | the model saves itself | CRUD, ERPs | the domain is the table; testing without a database |
| Data Mapper + Unit of Work | SQLAlchemy `Session`, Hibernate, Doctrine | plain objects; a session writes what changed | rich domains | implicit flushes and lazy loads |
| Database context | EF Core `DbContext`, Ecto `Repo`, Prisma — **ours** | one generic object per database: `get(T, id)`, `save(x)`, the transaction | one door, typed by `T` | grows: everything lands on one object |
| Repository per aggregate | Evans, Vernon, Cosmic Python | `Invoices.overdue()`, one class per root | the domain's questions by name | CRUD written again for every root |
| Capabilities that compose | Spring Data, Jakarta Data, Micronaut | small interfaces combined; each store implements what it honours | several stores, honestly | more types to name |
| The query as a value | SQLAlchemy `select()`, Ecto `Query`, jOOQ, Drizzle — our `Criteria` | the question is immutable data; the context runs it | composition, `explain` | — |
| CQRS | Axon, Marten | aggregates for writes, projections for reads | volume, cheap reads | two models to keep |
| Ambient transaction | Spring `@Transactional`, Django `atomic` | the transaction travels unseen | little noise | nobody sees what is inside |

Mature stacks converge on a generic context as the first door, the query as a value, declared
capabilities for what varies by store, an explicit transaction handle rather than an ambient one
(Ecto, EF Core and SQLAlchemy 2 all moved that way), and projections beside the aggregates.

**The shape ours takes** ([decision 27](decisions.md#27-a-baseline-capabilities-on-top-and-a-view-per-aggregate)):

    ddd.IRepository                 ReadsAggregates + WritesAggregates     the baseline, any store
      + Analyzes                   distinct · measures · group_by         when the store can fold
      + WritesInBulk               upsert · update_all · remove_all       when it can write past the aggregate
      + Transacts                  context · after_commit · after_rollback   when it has transactions
    repository.capabilities        StoreCapabilities                      what its engine honours

    MemoryRepository               baseline + Analyzes + WritesInBulk
    orm.Repository                 baseline + every capability + SQLAlchemy's own door

    AggregateRepository[T]         one aggregate's view over any repository, for named questions
    orm.DatabaseAggregateRepository[T]     the same over the database repository: analysis, bulk,
                                   context(), narrowed(), statement() / run(), session

**Two flavours of one repository.** The generic repository with a `Criteria` is the first door: it
answers everything, is built in, and is what most Features need — a repository class per aggregate,
written for every root, made simple things slow. The view per aggregate is where a name pays for
itself: it holds no state, every call goes through the repository underneath (hooks, scope,
version, named errors), and `context()` / `narrowed()` hand back the same class bound to the
transaction or the scope. The orchestrated path and the door underneath meet in it: a named
question is a `Criteria` by default, and a `statement()` taken further with SQLAlchemy when it
needs to be — answered in the same envelope.

**What is left.** A lock asked of an engine whose `capabilities.row_locks` is false — SQLite, which
drops `FOR UPDATE` — is still taken silently; per the rule *facilitate, never cage*, the next step
is a warning, not a refusal. A shared component (outbox, cron runs, auth) can now ask for exactly
the capabilities it calls; moving each to that is the work of each component.

---

## Maturity, area by area

| Area | Where it stands |
|---|---|
| One aggregate, querying, aggregation | **Mature** — matches Django/EF/Hibernate, with a query language they lack |
| Transactions and concurrency (relational) | **Mature** — the aggregate's version moves with its parts |
| Relations: reading | **Mature** — per page, per parent, across contexts |
| Relations: writing | **Partial** — safe and declared, but rebuilt beside `relationship()`; PRD_18 open |
| Collections in memory | **Mature** as an answer; the question side lacks sugar |
| Events and event sourcing | **Partial** — events as entities, the relay and event-sourced entities are built; snapshots, projection rebuilds and sagas are not |
| Volume and analytics | **Partial** — SQL-side solid, client-side hydrates |
| Many stores | **Started** — the baseline and capabilities are declared (§5); access plans and a second adapter are missing |
| Enterprise record concerns | **Partial** — the common ones built; fiscal numbering, money, request-bound rules missing |

## Roadmap

Ordered by what protects data first, then what removes the most hand-written code, then reach.

**Phase 1 — consistency (nothing silently wrong)**

1. ~~The aggregate's `version` moves with its parts.~~ Built: a part written, added or dropped
   updates the root with its version checked ([decision 28](decisions.md#28-the-version-is-the-aggregates)).
2. ~~Gapless numbering.~~ Built: `DatabaseNumbering.take(series, count, scope)` in the unit of work, and
   every repository on one `Database` joins the unit of work in play, so several Features commit
   together ([decisions 29–30](decisions.md#29-a-unit-of-work-is-joined-by-every-repository-on-its-database)).
3. ~~The outbox relay as a component.~~ Built as the context's event table and `EventRelay` —
   per-row delivery state, `SKIP LOCKED`, failure policies
   ([decision 31](decisions.md#31-a-bounded-contexts-events-are-entities-in-one-table), PRD_19).
4. ~~Event sourcing: an expected version on append.~~ Built: `EventSourcedMixin`, `entity_version`
   unique per entity; snapshots are next.

**Phase 2 — simplicity (less hand-written, nothing rebuilt)**

5. PRD_18 delegated to `relationship()` — the spike: the framework declares, SQLAlchemy executes.
6. A concise question: `Criteria.where(("state", "=", "posted"))` (the triple `@sincpro/criteria`
   already uses), named scopes composed with `&`/`|`/`~`, and `repository.query(T).where(…).search()`
   — never iterable, only terminal verbs run.
7. Projections without hydration: `repository.rows(T, criteria, fields=…)`, `arrow_batches`, a
   streamed `to_parquet`.
8. Checkpointed projections with rebuild; upcasters for event versions.

**Phase 3 — reach (the enterprise cases that drop to SQLAlchemy today)**

9. Read-only aggregates on views and materialized views (`map_view`, `refresh`).
10. Filters inside JSON (`"address.city"`), full-text search, both per dialect and index-backed.
11. Bulk load over `COPY` / ADBC (`repository.load`).
12. Sagas with compensation, grown from `workflows`.
13. Request-bound rules and `check_company`; a `Money` value object.
14. Read replicas; performance guards as warnings.

**Phase 4 — other stores**

15. Capabilities are declared (§5); a lock asked of an engine without row locks warns; declared
    access patterns and a planner; then a Mongo adapter, Elasticsearch as a read projection,
    Dynamo last.

Snapshots, crypto-shredding, a database inbox, temporal tables, attachments, CDC-shaped outbox
columns, Ibis and lakehouse sinks wait for a case that needs them.

## Sources

Each front's primary sources — Jakarta Data, Micronaut Data, Spring Data, AWS DynamoDB, MongoDB,
Elastic, EF Core, Prisma, Hibernate OGM; KurrentDB, Marten, Axon, `eventsourcing`, Debezium,
MassTransit, event-driven.io; SQLAlchemy, psycopg, ADBC, DuckDB, Ibis, TimescaleDB, pg_ivm;
Odoo, Django, EF Core filters, PostgreSQL sequences — are cited in the four research notes this
synthesis was written from. Claims the notes marked unconfirmed are not relied on above.
