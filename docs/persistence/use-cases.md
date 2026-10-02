# Persistence use cases — what the framework orchestrates, and the door underneath

Every use case a service meets with a database, from the simplest to the advanced, and where each
one is answered. The framework's promise is twofold:

- **Orchestrated by default.** The common case works the way an enterprise service needs it —
  consistent, versioned, scoped, traced — without the developer (or an agent) assembling it.
- **Never a cage.** Whatever the framework does not orchestrate, or orchestrates differently than
  a case needs, is reachable underneath: the model is a plain SQLAlchemy mapping, and
  `unit.session` is SQLAlchemy whole, inside the same transaction. Nothing has to be asked for.

The columns:

| Column | Meaning |
|---|---|
| **Vocabulary** | `sincpro_framework.ddd` — the abstract port and its types; what any store answers |
| **Framework · SQLAlchemy** | `sincpro_framework.orm` — what the adapter orchestrates |
| **SQLAlchemy directly** | the door underneath: `unit.session`, `statement()` → `run()`, the mapped model |
| **Status** | ✅ built · 🟡 partial · ⬜ gap — gaps are ranked at the end |

---

## 1. One aggregate

| Use case | Vocabulary | Framework · SQLAlchemy | SQLAlchemy directly | Status |
|---|---|---|---|---|
| Create | `Repository.save(entity)` | INSERT, `version = 1`, `created_at`/`updated_at` stamped | `session.add` | ✅ |
| Read by identity / natural key | `get`, `get_by(**values)` | one SELECT; archived left out | `session.get` | ✅ |
| Update, refusing a stale write | `save` + `StaleAggregate` | `UPDATE … WHERE version = :read` | `version_id_col` | ✅ |
| Delete / archive | `remove`, `archive` (`ArchivableMixin`) | DELETE / `archived_at` stamped; reads skip it | `session.delete` | ✅ |
| Who wrote it | `AuditedMixin` | `created_by`/`updated_by` from `Database(actor=…)` | — | ✅ |
| What changed | `ChangeTrackingMixin` → `EntityUpdated` | one event per save, per field `(before, after)` | `attributes.get_history` | ✅ |
| Rules on every write | `Hooks` (`before_save`, `after_create`, …) | run inside the write, refuse or compute | session events | ✅ |
| A batch of new aggregates | `save([...])` | one INSERT for N rows | `session.add_all` | ✅ |
| Name one aggregate's questions | `AggregateRepository[T]` over any store | `orm.DatabaseAggregateRepository[T]`: analysis, bulk, `context()`, `statement()` with the aggregate given | — | ✅ |

## 2. Querying

| Use case | Vocabulary | Framework · SQLAlchemy | SQLAlchemy directly | Status |
|---|---|---|---|---|
| Filter | `Criteria(where=…)`: `= != in not in > >= < <= between like is null contains not contains` | translated to WHERE | `select().where()` | ✅ |
| Combine | `all` / `any` / `negate` | AND / OR / NOT | `and_`, `or_`, `not_` | ✅ |
| Text search, case-insensitive | `contains` on text | `lower(col) LIKE '%…%'` | `ilike` | ✅ |
| Full-text search, ranked | — | — | `func.to_tsvector`, `@@` (Postgres) | ⬜ |
| Order | `order=[Sort]`, `parse_order("-total")` | ORDER BY + identity tiebreaker | `order_by` | ✅ |
| Page | `pagination` keyset (cursor) or offset | keyset by `(sort, id) < (…)` | `limit`/`offset` | ✅ |
| Count | `count` (`CAPPED` / `EXACT` / `NONE`) | capped at 10 000 unless asked | `func.count` | ✅ |
| Short questions | `exists`, `first`, `one`, `pluck`, `distinct`, `browse(ids)` | one statement each | — | ✅ |
| Every page, bounded memory | `stream`, `fetch_all` | page by page by keyset | `yield_per` | ✅ |
| A query the language cannot say | — | `statement(model, criteria)` → `run(...)`: a real `Select`, the envelope back | anything | ✅ |
| Strict input | unknown keys refused; `TOLERANT` for another version | — | — | ✅ |
| A tenant, a branch, a permission | `narrowed(scope)` | every read AND-ed, every write checked | — | ✅ |

## 3. Aggregation and analysis

| Use case | Vocabulary | Framework · SQLAlchemy | SQLAlchemy directly | Status |
|---|---|---|---|---|
| Totals over the whole set | `measures(sum/avg/min/max/count/percentile)` | one statement, in the database | `func.*` | ✅ |
| Group by, several levels, date grains | `Grouping(group_by=[Level(field, grain)])` | GROUP BY, buckets with ids and a page each | `group_by` | ✅ |
| Filter on a measure | `where_measures` | HAVING | `having` | ✅ |
| Pivot with margins | `pivot(rows, columns, measures)` | four statements whatever the volume | — | ✅ |
| Export | `export` (page by page as dicts) | streaming | — | ✅ |
| What it will cost | `explain` | the SQL, the dropped, the statement count | `compile()` | ✅ |
| DataFrames | `data_analysis`: a `Criteria` into pandas / polars / DuckDB / Arrow | read once, page by page | — | ✅ |
| Window functions | — | through `statement()` → `run()` | `func.row_number().over()` | 🟡 escape hatch only |
| A view, a materialized view | — | an aggregate can be mapped on a view's `Table`, read-only by convention | `CREATE MATERIALIZED VIEW`, `REFRESH` | ⬜ no declared read-only aggregate |
| A report joining several aggregates | — | a field kept current by an event (`Line.entry_state`), or `statement()` | joins | 🟡 pattern + escape hatch |

## 4. Relations — reading

| Use case | Vocabulary | Framework · SQLAlchemy | SQLAlchemy directly | Status |
|---|---|---|---|---|
| many2one / one2many by a foreign key | `list[T]` / `T \| None` annotations, inferred | resolved once per page, never per row | `relationship` + `selectinload` | ✅ |
| By a business key (FK to a unique column) | inferred from the FK | `parent_field` / `related_field` | `primaryjoin` | ✅ |
| many2many | `Relation.many_to_many(through=…)` | one statement per node | `secondary=` | ✅ |
| Ids held in a JSON column | `Relation.id_list` | one statement per node | — | ✅ |
| From another bounded context | `Relation.bus(...)`, `Relation.resolved_by(...)` | one call per node, the criteria reflected | — | ✅ framework-only |
| What to bring, at any depth, per parent paged | `specification` | window function: N per parent | — | ✅ framework-only |
| A relation's own filter | `scope=Criteria(...)` | AND-ed into every reading of it | `with_loader_criteria` | ✅ |
| Lazy inside a unit of work, refused outside | `RelationNotResolved` | resolves whole on first touch | `lazy="raise"` | ✅ |

## 5. Relations — writing

| Use case | Vocabulary | Framework · SQLAlchemy | SQLAlchemy directly | Status |
|---|---|---|---|---|
| Save a root with its children | `save(root)` | children written with the root's key | `cascade="save-update"` | ✅ |
| Replace the children | assign the list | settles what the last whole reading saw, as `orphans` declares | collection replace | ✅ |
| What a dropped child becomes | `Orphans.REFUSE` (default) / `DELETE` / `DETACH` | refused before any statement / deleted / key NULL | `delete-orphan`, nullify | ✅ |
| Remove a root with its parts | `remove(root)` | as `orphans` declares; refused by default | `cascade="delete"` | ✅ |
| A reference to another aggregate | `owned=False` | read, never written | `viewonly=True` | ✅ |
| Add / remove one child | — | — | `collection.append` / `.remove` | ⬜ PRD_18 |
| `+=` / `-=` as Odoo | — | — | — | ⬜ PRD_18 |
| Assign the parent, moving the FK | — | ignored today | `back_populates` | ⬜ PRD_18 |
| Link / unlink a many2many pair | — | — | `secondary=` collection | ⬜ PRD_18 |
| Writing a cross-context relation | — | — (must be refused) | — | ⬜ PRD_18 |

## 6. Collections in memory

| Use case | Vocabulary | Framework · SQLAlchemy | SQLAlchemy directly | Status |
|---|---|---|---|---|
| Set algebra by identity | `EntityCollection`: `\|`, `&`, `-`, `^` | — | — | ✅ |
| Sub-filter what is in hand | `filtered(fn)`, `filtered_by(criteria)` | the same evaluator the SQL is specified by | — | ✅ |
| Transform, index, group | `mapped`, `index_by`, `grouped`, `partition`, `sorted_by` | — | — | ✅ |
| Slice | `first`, `last`, `take`, `skip`, `chunk`, `ensure_one` | — | — | ✅ |
| Fold | `sum_by`, `average_by`, `min_by`, `max_by`, `count_where` — refused on a partial page | — | — | ✅ |
| Diff two readings | `changes_against` | — | — | ✅ |
| Know a page is not the whole | `is_partial`, `count`, `cursor` | — | — | ✅ |
| A relation as a writable set | — | — | — | ⬜ PRD_18 |

## 7. JSON and semi-structured data

| Use case | Vocabulary | Framework · SQLAlchemy | SQLAlchemy directly | Status |
|---|---|---|---|---|
| A JSON column | `dict` / pydantic field | `JsonText` | `JSON`, `JSONB` | ✅ |
| A value object stored as JSON | pydantic model field | `EMBEDDED`: described, masked by a specification, read back as the model | — | ✅ |
| Text per locale | `dict[str, str]` with a `default` | `TranslatedText`: one JSON object per value, searchable with `like` as written | — | ✅ |
| A list held in a column contains a value | `contains` on `text[]` | substring on the stored JSON (portable) | `@>` on `jsonb` | 🟡 portable, not indexed |
| Filter on a path inside JSON (`address.city = 'La Paz'`) | — (dropped: `unknown_field`) | — | `col["address"]["city"].astext`, `jsonb_path_exists` | ⬜ |
| A GIN index for JSON search | — | declared on the table, as any `Index` | `Index(..., postgresql_using="gin")` | 🟡 declarable, the language does not use it |

## 8. Transactions and concurrency

| Use case | Vocabulary | Framework · SQLAlchemy | SQLAlchemy directly | Status |
|---|---|---|---|---|
| Several writes, one transaction | — | `context()` | `session.begin()` | ✅ |
| Several Features, one transaction | — | a repository on the same `Database` joins the unit of work in play | one session passed around | ✅ |
| A transaction of its own inside another | — | `context(separate=True)` | a second session | ✅ |
| Only what was saved is written | — | `Writes.SAVED` (default) / `Writes.CHANGED` | — (no such mode) | ✅ framework-only |
| A part that can fail alone | — | `savepoint()` | `begin_nested` | ✅ |
| A long job, durable batch by batch | — | `commit()` inside the block | `session.commit` | ✅ |
| Isolation, read-only, statement timeout | — | `context(isolation=, read_only=, timeout=)` | `execution_options` | ✅ |
| Engine options as they come | — | `context(engine={...})` | `execution_options` | ✅ |
| Row locks | `for_update`, `skip_locked`, `nowait` (on the port) | `FOR UPDATE [SKIP LOCKED \| NOWAIT]` | `with_for_update` | ✅ |
| Optimistic lock | `version` → `StaleAggregate` | `version_id_col` | — | ✅ |
| An aggregate's version moves with its parts | `save(root)` + `StaleAggregate` | a part written, added or dropped updates the root with its version checked and raised | `version_id_col` on the root | ✅ |
| Errors named by fact | `DuplicateAggregate`, `ConstraintViolation`, `TransactionConflict`, `TimedOut` | SQLSTATE / errno / SQLite name | driver exceptions | ✅ |
| Run a lost race again | — | `retrying(work)` | — | ✅ |
| Act once this transaction committed | — | `after_commit` / `after_rollback`, savepoint-aware | session events (global) | ✅ |

## 9. Bulk writes and synchronisation

| Use case | Vocabulary | Framework · SQLAlchemy | SQLAlchemy directly | Status |
|---|---|---|---|---|
| Idempotent import / mirror another system | `upsert(on=, update=)` → `Upserted(written, skipped)` | `ON CONFLICT … RETURNING`, bounded by the scope | `insert().on_conflict_do_update` | ✅ Postgres, SQLite |
| Mass update / delete by filter | `update_all`, `remove_all` | one statement; version raised; a page or an unanswerable condition refused | `update()`, `delete()` | ✅ |
| Raw SQL, COPY, a stored procedure | — | — | `session.execute(text(...))`, the DBAPI connection | ✅ escape hatch |

## 10. Events and distribution

| Use case | Vocabulary | Framework · SQLAlchemy | SQLAlchemy directly | Status |
|---|---|---|---|---|
| Record a fact on the aggregate | `DomainEvent`, `record` / `pull_events` | — | — | ✅ |
| Keep the context's events, with the change | `DomainEvent` is an `Entity`; `save(aggregate)` keeps what it recorded; `MemoryRepository` keeps them the same way in a test | `event_table` + `map_events(registry, ContextDomainEvent, table)`: one table, single-table inheritance by `name`, subclass fields in `payload` | — | ✅ |
| Find events by class, and by entity | `search(InvoicePaid, criteria)`, `search(ContextDomainEvent, criteria)` | envelope columns filtered like any column | `statement()` | ✅ — by their own fields: filters inside JSON (gap) |
| One entity's history | `Criteria` on `entity_type` / `entity_id`, ordered by `id` | indexed `(entity_type, entity_id, id)` | — | ✅ |
| An event about no entity | `save([DayClosed()])`, `Publisher(RepositoryQueue(repository))` | `entity_type` / `entity_id` empty | — | ✅ |
| Event sourcing | `EventSourcedMixin`: `happened`, `apply`; `get` rebuilds, `save` appends | `entity_version` + `UNIQUE(entity_type, entity_id, entity_version)` → `StaleAggregate` | — | ✅ — snapshots next |
| Mark what leaves, and where its delivery stands | `DeliverableEventMixin`: `delivered_at`, `next_delivery_at`, `delivery` | `is_deliverable` column; a partial index on what is still to deliver | — | ✅ |
| Publish only what committed | `EventRelay(repository, source, publisher)` over any repository | `FOR UPDATE SKIP LOCKED` inside a unit of work; `Crons.run_relay`, `relay_deliverable_events` | — | ✅ at least once, in order per entity |
| Decide what a failure does | `RetryInPlace`, `RetryLater`, `ParkAndContinue`, `SkipAndContinue`; `ExponentialBackoff`, `FixedBackoff` | — | — | ✅ |
| Projections kept from events | a Feature subscribed to the event saves a read model | — | — | 🟡 no rebuild helper |
| Old events read by new code | — | — | — | ⬜ upcasters |
| Deliver across services | `Publisher`, brokers (FastStream), inbox | at least once + idempotent handler | — | ✅ |
| A process across contexts that compensates | — | — | — | ⬜ sagas |

## 11. Schema, stores, tests

| Use case | Vocabulary | Framework · SQLAlchemy | SQLAlchemy directly | Status |
|---|---|---|---|---|
| Migrations across contexts and stores | `migrations` | Alembic chains, one timeline | Alembic | ✅ |
| Several databases | one `Database` per context | — | several engines | ✅ |
| A test without a database | `MemoryRepository` | — | — | 🟡 no relations, no transactions |
| A test on a real engine | — | SQLite in memory; Postgres via `SINCPRO_POSTGRES_URL` | — | ✅ |

## 12. Many stores

| Use case | Vocabulary | Framework · SQLAlchemy | SQLAlchemy directly | Status |
|---|---|---|---|---|
| Know what a store can answer | `Analyzes`, `WritesInBulk`, `Transacts`; `StoreCapabilities` | `capabilities` read off the dialect | `engine.dialect` | ✅ |
| Lock rows (`for_update`, `skip_locked`, `nowait`) | on the baseline; `capabilities.row_locks` says whether the engine holds them | `FOR UPDATE` per dialect | `with_for_update()` | 🟡 declared, not yet enforced: SQLite drops `FOR UPDATE` without a word |
| See how a question is answered: key, range, index, scan | `explain` | statements and plans | `EXPLAIN` | 🟡 relational only; no access-pattern planner |
| A document store (Mongo) | the port | — | — | ⬜ adapter; owned children become one document |
| A search projection (Elasticsearch) | the port, read-only | — | — | ⬜ |
| A key-value store (Redis) | `KeyValueStore` | — | — | ✅ outside the aggregate port, on purpose |

## 13. Volume and analytics

| Use case | Vocabulary | Framework · SQLAlchemy | SQLAlchemy directly | Status |
|---|---|---|---|---|
| Walk millions of rows | `Cursor` (keyset) | `iterate` | `yield_per` | ✅ |
| Read columns without building aggregates | — | — | `select(columns)` → `run()` | ⬜ `rows(...)` projection |
| Arrow and Parquet at millions of rows | `export`, `data_analysis` | — | ADBC | 🟡 every row is hydrated first |
| Load a large file | — | `add_all`, `upsert` | `COPY` (psycopg) | ⬜ `load()` over `COPY`/ADBC |
| Send reads to a replica | — | — | a second engine | ⬜ |
| Warn on an expensive query at run time | `explain` | — | — | ⬜ guards (statements, rows, ms) |
| Partitioned and time-series tables | — | migrations | declarative partitions | 🟡 nothing warns when a query misses the partition key |

## 14. Records and enterprise concerns

| Use case | Vocabulary | Framework · SQLAlchemy | SQLAlchemy directly | Status |
|---|---|---|---|---|
| Write a condition briefly | `Criteria` | — | `where()` | 🟡 correct and verbose: no `where(("state", "=", "posted"))` triple |
| Named scopes that compose | `combined()` | — | — | 🟡 no `&` / `\|` / `~` |
| Gapless numbering per company, series and year | `INumbering.take(series, count, scope)`, `MemoryNumbering` | `orm.DatabaseNumbering` over `numbering_table`: one `ON CONFLICT … RETURNING`, given back by a rollback | a locked counter row | ✅ |
| Rules bound to the request (tenant, company) on every read and write | `narrowed` | — | — | 🟡 applied by hand; no `check_company` |
| Money: amount, currency, rounding | — | — | — | 🟡 a rule, no type |
| Stored derived fields that can be searched | hooks | — | — | 🟡 computed by hand |
| Read as of a date | — | — | — | ⬜ temporal reads |
| Attachments and blobs | — | — | — | ⬜ |
| Erase a person (GDPR) | — | — | — | ⬜ crypto-shredding for events |

---

## The gaps, ranked

Ordered by what protects data first, then what removes the most hand-written code, then reach. The
reasoning behind each, front by front, is in [landscape.md](landscape.md).

| # | Gap | Why it matters | Direction |
|---|---|---|---|
| 1 | Snapshots of an event-sourced entity | a rebuild reads every event of the entity | a snapshot every N versions, the rebuild starting from it |
| 2 | Writing relations: add/remove, `+=`/`-=`, many2one assignment, many2many link/unlink | the domain cannot say "this line goes" without rebuilding the list; assigning a parent is ignored | PRD_18 — delegated to `relationship()` (the spike: the framework declares, SQLAlchemy executes) |
| 3 | A concise question | conditions are correct and verbose; scopes do not compose | the `["field", "op", value]` triple of `@sincpro/criteria`, scopes with `&`/`\|`/`~`, a never-iterable `query(T)` |
| 4 | Reading without hydration | `export` and analytics build every aggregate before Arrow — the slowest path there is | `rows(...)`, `arrow_batches`, a streamed `to_parquet` |
| 5 | Projections and upcasters | read models cannot be rebuilt; old events break new code | a projection rebuilt from the event table; versioned events read through upcasters |
| 6 | Read-only aggregates on views / materialized views | reporting across aggregates without joins in Features | `map_view` — reads through `Criteria`, writes refused, `refresh()` |
| 7 | Filter inside JSON; full-text search | semi-structured data and ranked search in every enterprise system | `"address.city"` and a `search` operator, per dialect, index-backed |
| 8 | Bulk load | `add_all` tops out long before a million rows | `load()` over `COPY` / ADBC |
| 9 | Sagas | a process across contexts that has to undo its steps | the next iteration, over the event table and the relay |
| 10 | Request-bound rules, `check_company`, `Money` | multi-company data leaks across a hand-forgotten filter | rules bound to the context, checked on read and write |
| 11 | Replicas and run-time guards | reads crowd the primary; an N+1 reaches production unseen | `Database(url, replicas=[…])`; warnings, never refusals |
| 12 | Access patterns and other stores | a second store needs to say how it answers, not only what | capabilities are declared (`Analyzes`, `WritesInBulk`, `Transacts`, `StoreCapabilities`); next: a lock asked of an engine without row locks warns, a key/range/index/scan planner, then Mongo |
| — | Window functions | rankings, running totals | stay on `statement()` → `run()` until a case repeats |
| — | `MemoryRepository` without relations or transactions | a Feature tested on the double can behave differently | persistence is tested on SQLite; the double is for Features' logic |

Each gap keeps the two promises: orchestrated by default when it lands, and reachable today
through the door underneath.
