# Design

## Two packages, one line

```
sincpro_framework/
├── ddd/                          the vocabulary · no database imported anywhere
│   ├── entity/
│   │   ├── entity.py             Entity: id (UUID v7), created_at, updated_at, version, translations()
│   │   ├── entity_collection.py  EntityCollection, Count, Dropped, identity_of / identity_name
│   │   ├── model_meta.py         Meta, FieldMeta, FieldType, annotations_of, related_class
│   │   ├── relations.py          Relation (resolved_by, bus), Resolver, BusResolver, resolve_elsewhere
│   │   └── mixins/               what an aggregate opts into, one file each
│   │       ├── audited.py        AuditedMixin: created_by, updated_by
│   │       ├── archivable.py     ArchivableMixin: archived_at, archive(), restore()
│   │       └── tracking.py       ChangeTrackingMixin, EntityUpdated: what counts as a change
│   ├── criteria/
│   │   ├── criteria.py           Criteria, Condition/All/Any_/Not, Sort, Grouping, Level, Bucket, Specification
│   │   ├── pagination.py         Pagination, Cursor (keyset), Offset, CursorKeys
│   │   └── evaluate.py           matches(): the in-memory evaluator, the specification of the translator
│   ├── repositories/
│   │   ├── repository.py         Repository: the abstract store a use case is written against
│   │   ├── memory_repository.py  the same vocabulary answered over records held in memory
│   │   ├── hooks.py              Hook, Hooks, Rule: what a project puts around its aggregates
│   │   └── change_tracking.py    change tracking for a store with no flush to ask
│   ├── query.py                  Query, ResponsePaginatedQuery (the answer, masked on the wire)
│   ├── events.py                 DomainEvent, EventTrackableMixin, EventStatus
│   ├── value_object.py           ValueObject
│   └── exceptions.py             DomainError, InvalidCriteria, ContractViolation, StaleAggregate, DuplicateAggregate, RelationNotResolved
└── orm/sqlalchemy/               the adapter · the [sqlalchemy] extra
    ├── database.py               Database: engine, session factory, observed from birth, and the two
    │                             things it guarantees about every write — who, and what changed
    ├── repository.py             Repository: the reads, the writes, context, narrowed, pivot, explain
    ├── change_tracking.py        the diff the engine is about to write, at before_flush
    ├── sql_translator.py         Criteria → Select; grains per dialect
    ├── data_mapper.py            entity_table, map_aggregates, Relation (foreign_key, many_to_many, id_list), foreign-key inference
    ├── model_introspection.py    describe(): the class and its table → Meta
    ├── relation_resolver.py      the database kinds of relation, one statement per node
    ├── custom_fields.py          JsonText, TranslatedText
    └── observability.py          every statement to the logger, the tracer and the error tracker
```

The line between the two is a test: `tests/orm/test_optional_extra.py` imports the vocabulary
with `sqlalchemy` blocked. Anything under `ddd/` that needed SQLAlchemy would fail there.

## The flow of a read

```
Criteria
  │  1. describe(model) → Meta                       cached per class, read off annotations and table
  │  2. Meta.accept(where)                            unusable conditions dropped and named
  │     Meta.accept_specification(specification)      unknown names dropped and named
  │  3. sql_translator: WHERE, ORDER BY with tiebreaker, keyset resume
  ▼
SELECT … LIMIT n+1                                     one row past the limit says "there is more"
  │  4. count: free when the first page was short; else capped (LIMIT cap+1) or exact
  │  5. relation_resolver: per named relation, ONE statement (or one call) for all the rows,
  │     partitioned by parent, ordered and cut per parent; then its own specification, recursively
  ▼
EntityCollection(items, cursor, count, dropped, meta)
  │  6. ResponsePaginatedQuery.of(page, criteria): meta cut by the mask; on the wire the
  │     records show the named scalars + identity, embedded values cut inside, relations by name
  ▼
JSON
```

Steps 1 to 4 are `Repository.prepare`, `_statement_from`, `_page` and `_counted`. Step 5 is
`resolve_relations`. Step 6 is the answer's own serializer: inside the process the records stay
whole and typed; the mask exists only on the wire.

## The flow of a write

```
with self.repository.context() as repository:      one session, one transaction
    entry = repository.get(Entry, entry_id)          the aggregate as stored
    entry.post()                                     rules run in the domain; events recorded in memory
    entry.lines                                      a relation touched here resolves whole, lazily
    repository.save(entry)                           UPDATE … WHERE version = :loaded  → StaleAggregate if moved
    recorded = entry.pull_events()                   explicit
publisher.publish(recorded[0])                       after the commit, never inside it
```

`save` is an INSERT for a new aggregate and a conditional UPDATE for a loaded one; the version
check is SQLAlchemy's `version_id_col` switched on by `map_aggregates` for every `Entity`.
`updated_at` is stamped by the session on every flush that changed something. `savepoint()` is
the checkpoint a batch uses so one failing group does not undo the others.

## The extension points

| To add | Write | Touch the core? |
|---|---|---|
| A way to bring a relation from a new place (HTTP, Kafka, a cache) | a callable `(keys, criteria) -> records` and declare it with `Relation.resolved_by` | no |
| Another bounded context as the other side | `Relation.bus(Related, bus, Command, identified_by=…)`; the other side is an ordinary `search` | no |
| A date grain for another SQL dialect | `register_grain_translator("mysql", translator)` | no |
| A column type of the project's own | a SQLAlchemy `TypeDecorator`; `describe()` reads the annotation, not the column type | no |
| A second persistence backend | a package beside `orm/sqlalchemy/` inheriting `Repository` and implementing `describe()` | — |
| A tenant, a branch, a permission | `repository.narrowed(criteria)`, handed to the bus instead of the wide one | no |
| Who wrote a record | `AuditedMixin` on the aggregate, `Database(url, actor=…)` | no |
| Deleting that keeps the row | `ArchivableMixin` on the aggregate | no |
| A Feature tested without a database | `MemoryRepository(*records)` as the dependency | no |

## The invariants every change must keep

1. **Nothing under `ddd/` imports a database.** The optional-extra test says so.
2. **A relation is resolved once per node for a whole page, never per row.** The tests count
   statements; a shape that passes but adds a statement per row fails.
3. **What cannot be answered is dropped and said, never raised**, for filters and specifications
   alike. A shared link outlives a schema.
4. **The identity always travels.** No mask removes it.
5. **The mask exists on the wire only.** A Feature sees the whole, typed aggregate.
6. **Inside a unit of work relations resolve whole; outside, an unrequested one refuses.** No
   silent N+1.
7. **Nothing that reaches SQL comes from the wire unvalidated.** Field names pass through `Meta`,
   fold functions through a closed list, values through the field's own reader.
8. **Defaults, never ceilings.** What the client said is what the client gets.
9. **Events are recorded in memory and published by a Feature after its unit of work.** The
   framework stores none.
10. **A narrowed repository never reads wide.** An aggregate that cannot express the scope is
    refused; a scope that is silently dropped would hand over the whole table.
