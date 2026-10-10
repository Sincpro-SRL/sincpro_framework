# PRD_17: Transactional persistence — aggregates, units of work and what the engine reports

- **Status**: built — §2 to §8, on SQLite and Postgres, documented in
  [transactions.md](../persistence/transactions.md) and [relations §8](../persistence/relations.md),
  decided in [decisions §20–§23](../persistence/decisions.md). Two things changed on the way and
  the sections below say how: an orphan is a child a reading saw and an assignment dropped
  (§2.2), `remove` takes the parts and leaves a declared reference to its foreign key (§2.4), and §5 names three new
  exceptions, not four. Every change came from a reproduced failure (decisions §20). Issues it answers: #132, #134, and the half of #131 that
  needs a hook on the commit. #133 (a foreign key to a business key) is the base of §2.
- **Depends on**: `Repository` (`orm/sqlalchemy/entrypoint/repository.py`), the relation descriptors and
  inference (`data_mapper.py`), the resolver (`relation_resolver.py`), the `ddd` port and its
  exceptions, `Database` and its session events.
- **Scope**: the SQLAlchemy `Repository`, which is what every service types and uses. The `ddd`
  port receives only the words that pass the [manifesto](../persistence/manifesto.md)'s rule 3
  (§10); `MemoryRepository` honours them or refuses them, never ignores them.
- **Philosophy**: an aggregate is saved whole, through one door, inside one transaction. Each
  capability exists twice: as a **word** the framework understands and enforces (`cascade`,
  `isolation`, `nowait`, `upsert`), and as a **passthrough** that hands SQLAlchemy what it
  takes, as it takes it. A word the engine cannot honour is refused when honouring it is
  correctness, and warned about when it is only performance.

## Problem

The repository writes and reads one aggregate well. It stops there:

- **A root does not save its children.** `save(workspace)` writes the workspace and ignores
  `workspace.repositories`, without a warning. `sincpro_forge` repeats *remove the previous
  children, stamp their foreign key, save them* for three aggregates, and forgetting the remove
  left rows from a previous rebuild attached (#132).
- **A unit of work takes no options.** No isolation level, no read-only transaction, no
  statement timeout. A Feature that needs `SERIALIZABLE` opens `unit.session` and writes
  SQLAlchemy by hand, outside every promise the repository makes.
- **What the engine reports is flattened.** Every `IntegrityError` becomes `DuplicateAggregate`
  — a foreign key violation and a `NOT NULL` violation included. A serialization failure or a
  deadlock reaches the Feature as a raw driver error, so `retrying` cannot retry it.
- **No upsert, no write by criteria.** An import that must be idempotent reads every row first
  or drops to raw SQL; a mass `UPDATE … WHERE` loads every record.
- **No hook on this transaction's commit.** `Database.after_commit` is global, for every session
  the process opens. A Feature that must publish only if *its* write committed has no door.

## Background — what mature libraries settled on

| Library | Its shape | What this spec takes |
|---|---|---|
| **EF Core** | owned types (`OwnsMany`) saved and deleted with the owner; a severed *required* dependent is deleted, an *optional* one gets `NULL`; `ExecuteUpdate`/`ExecuteDelete` | composition by default; orphans by foreign key nullability; `update_all`/`remove_all` |
| **JPA / Hibernate** | `@OneToMany(cascade = ALL, orphanRemoval = true)`; `@Version`; `LockModeType.PESSIMISTIC_WRITE` with a lock timeout hint | cascade on save and remove; the version check on children too |
| **Spring** | `@Transactional(isolation, readOnly, timeout)`; `TransactionSynchronization.afterCommit` | options per unit of work; `unit.after_commit` |
| **Django** | `atomic()`; `on_commit(fn, robust=)`; `select_for_update(nowait, skip_locked, of)`; `bulk_create(update_conflicts=…)`; `QuerySet.update()` bypasses `save()` | `after_commit` that never undoes a commit; `nowait`; `upsert`; bulk writes outside hooks, said plainly |
| **Rails** | `has_many dependent: :destroy`, `autosave`; `transaction(isolation:)`; `after_commit`; `upsert_all(unique_by:)`; `update_all` skips callbacks | the same set, with the same caveats |
| **Ecto** | `cast_assoc` with `on_replace: :delete \| :nilify`; `insert_all(on_conflict:, conflict_target:)`; `update_all` | delete vs. nilify as the two orphan outcomes; `upsert(on=…)` |
| **SQLAlchemy** | `relationship(cascade="all, delete-orphan")`; `execution_options(isolation_level=…, postgresql_readonly=…)`; `insert().on_conflict_do_update`; `with_for_update(nowait, skip_locked)` | what the adapter maps every word to |

Every one of them separates **composition** (the child is part of the aggregate) from
**reference** (the child is another aggregate), and every one of them makes bulk writes skip the
per-record machinery and says so.

**Why not `relationship(cascade=…)` itself.** The framework's relations are its own descriptors,
not SQLAlchemy relationships: they resolve once per page, refuse lazy loading outside a unit of
work, and resolve across bounded contexts through a bus. Mapping `relationship()` would bring
SQLAlchemy's lazy loading back and two sources of truth for one relation. The cascade is
implemented in `save`, on the descriptors the framework already owns; SQLAlchemy is used for
what it is best at — the flush, its ordering, the statements.

## The specification

The words **MUST**, **MUST NOT**, **SHOULD** and **MAY** are used as in RFC 2119.

### 1. Terms

- **Root**: the aggregate handed to `save` or `remove`.
- **Owned relation**: a to-many whose records are part of the root. Saved and removed with it.
- **Reference**: a relation to another aggregate. Read through the root, never written by it.
- **Orphan**: a stored child of an owned relation that is no longer in the root's collection.
- **Touched**: a relation whose value was assigned in memory, or resolved whole.

### 2. Owned relations — the cascade

**2.1 What is owned.** A to-many inferred from a foreign key is **a part of its root by
default** — Evans' aggregate: `save` writes its children, an assignment settles its orphans, and
`remove(root)` takes them along. A reference to another aggregate is declared:

```python
map_aggregates(registry, tables, relations={
    Customer: {"invoices": Relation.foreign_key(Invoice, identified_by="customer_id", owned=False)},
})
```

Vernon's rule keeps that declaration rare — an aggregate holds another by id, not as a collection
— and the first consumer measured it: every one2many of `sincpro_forge` is a part. *Narrowed for a
while to "removed only when declared owned", after `remove(author)` deleted every `Work` of a
model holding another aggregate as a collection; that made "part of the parent" mean one thing for
`save` and another for `remove`, and was reverted with that evidence (decisions §20).* A to-one is
never owned, and `owned` is read only on a to-many.

**2.2 When `save(root)` cascades.** For every owned relation of the root:

| The relation is | `save` does |
|---|---|
| never read nor assigned | nothing; no statement is issued for it |
| read — whole, a page or filtered | writes the children that are new or changed; removes nothing |
| assigned since the last write | writes them and settles the orphans (2.3) |
| assigned over a page or a filtered reading | writes them, removes nothing, logs a warning naming the relation |

Only an assignment says which children are gone. A reading — even a whole one — says what was
there when it was read, and settling against it deletes what another transaction added since; a
list a constructor gave is an assignment already written once the root is stored. *Specified at
first as «touched and whole»; that version deleted an invoice saved separately, because the
customer still held the empty list of its constructor (decisions §20).*

**2.3 What is written.** In the same flush as the root, and through the same path as any saved
record — hooks, `version`, `updated_at`, change tracking:

1. Each child receives the root's key on its foreign key (`parent_field` → `related_field`); the
   domain never sets it.
2. New children are inserted; loaded children are updated, with their own `version` check.
3. Orphans are settled by what the relation declares:

| `orphans=` | Orphan outcome | Mirrors |
|---|---|---|
| `Orphans.REFUSE` (default) | the write is refused before any statement, naming the relation | Ecto `:raise`, EF Core `Restrict` |
| `Orphans.DELETE` | `DELETE` | Ecto `:delete`, JPA `orphanRemoval`, EF Core `Cascade` |
| `Orphans.DETACH` | `UPDATE … SET fk = NULL` | Ecto `:nilify`, EF Core `SetNull` |

*Specified at first by the key column — `NOT NULL` deletes, nullable sets NULL, EF Core's
default. That made `remove(customer)` delete every invoice when nobody asked; both destructive
outcomes are declared now (decisions §20).*

Orphans are found with one `SELECT` of child identities per owned relation for the whole batch,
and settled with one statement per outcome. The cascade is recursive: a child's own owned
relations cascade when touched. A record visited twice in one `save` is written once.

**2.4 `remove(root)`.** Children of every relation the root owns are settled as orphans (2.3)
before the root is deleted — read by the foreign key alone, whatever the relation's scope shows.
A reference (`owned=False`) is left to its foreign key, which refuses the delete while children
point at the root.

**2.5 `archive(root)`** does not cascade (open question 3).

**2.6 Collections are replaced, not mutated.** A resolved `EntityCollection` is immutable;
changing the children means assigning a new list: `workspace.repositories = [*kept, added]`.

### 3. Unit-of-work options

```python
with repository.context(isolation=Isolation.SERIALIZABLE, read_only=False, timeout=5.0) as unit: ...
with repository.context(engine={"postgresql_readonly": True}) as unit: ...
```

| Option | Meaning | SQLAlchemy | Not supported by the dialect |
|---|---|---|---|
| `isolation` | `"read_committed"`, `"repeatable_read"`, `"serializable"` | `connection.execution_options(isolation_level=…)` before the first statement | refused — `ContractViolation` |
| `read_only` | no write may leave this unit of work | `save`/`remove`/`archive`/`upsert`/`*_all` refuse before any statement; `SET TRANSACTION READ ONLY` where the dialect has it | always honoured: the refusal is the framework's |
| `timeout` | seconds any one statement may run | `SET LOCAL statement_timeout` (Postgres) | warning, statement runs unbounded |
| `engine` | a mapping handed to `execution_options` as is | passthrough | whatever SQLAlchemy does with it |

The options apply to the outermost `context()`. A nested `context()` that asks for different
options MUST be refused (the transaction has already begun); one that asks for none, or the same,
joins it.

### 4. Locking

`get` and `search` gain `nowait: bool = False` beside `for_update` and `skip_locked`, mapped to
`with_for_update(nowait=True)`. `nowait` and `skip_locked` together are refused, and so is either
without `for_update`. A row held elsewhere under `nowait` raises `TimedOut` (§5).
`MemoryRepository` refuses all three as it refuses `for_update`. A subclass that overrides
`search` or `get` gains the parameter too: the type checker holds it to the port.

### 5. What the engine reports

The adapter MUST translate the driver's error by its SQLSTATE (or the dialect's equivalent) into
the `ddd` exception that names the fact, carrying the engine's message. One exception per thing
the caller does differently — fix the write, or run it again — not one per code (Sincpro coding
style, principle 5):

| SQLSTATE | MySQL | SQLite | Exception | New |
|---|---|---|---|---|
| `23505` | 1062 | `UNIQUE`, `PRIMARYKEY` | `DuplicateAggregate` | — |
| `23503`, `23502`, `23514` | 1451, 1452, 1048, 3819 | `FOREIGNKEY`, `NOTNULL`, `CHECK` | `ConstraintViolation(ContractViolation)` | yes |
| `40001`, `40P01` | 1213, 1205 | `BUSY`, `LOCKED` | `TransactionConflict(DomainError)` | yes |
| `55P03`, `57014` | 3572 | — | `TimedOut(DomainError)` — not retried | yes |

An error with no SQLSTATE (SQLite) is matched by the dialect's message; what matches nothing is
re-raised untouched. `retrying` defaults to `on=(StaleAggregate, TransactionConflict)` — the two
races a fresh read resolves.

### 6. Upsert

```python
repository.upsert(lines, on=("ledger_id", "number"), update=("amount", "state"))
```

- `on` names a unique key that MUST exist on the table; anything else is refused.
- `update` names the columns a conflict overwrites; omitted, every column but `on` and the
  identity. `update=()` inserts or leaves the stored row as it is.
- A conflicting row gets `version = version + 1` and `updated_at` stamped. **No `version` check**:
  an upsert overwrites by definition, and the docstring says so.
- `before_save`/`after_save` hooks run; `before_create`/`before_update` do not, because which one
  happened is known only to the database. Change tracking emits nothing.
- `insert().on_conflict_do_update … RETURNING` on Postgres and SQLite (3.35+); any other dialect,
  MySQL included, is refused — `ON DUPLICATE KEY` matches any unique key and cannot count what it
  wrote. It answers `Upserted(written, skipped)` per distinct key. Owned relations are not
  cascaded.

### 7. Writes by criteria

```python
count = repository.update_all(Invoice, criteria, {"state": "void"})
count = repository.remove_all(Session, criteria)
```

One statement each; the number of rows is the answer. The repository's scope (`narrowed`) and
archived rows apply exactly as in a read. **Hooks, `version` checks, cascades and change tracking
do not run** — the docstring opens with it, as Django's and Rails' do. `version` is still raised
and `updated_at` stamped on `update_all`, so a later `save` of a stale copy is still refused.

This reverses a rejection in `decisions.md` §12 ("update or delete by criteria … a generic write
path skips the aggregate's rules"); see *Decisions* and open question 5.

### 8. Hooks on this transaction

```python
with repository.context() as unit:
    unit.save(invoice)
    unit.after_commit(lambda: publisher.publish_all(invoice.pull_events()))
```

- `after_commit(fn)` runs `fn` once, after the transaction that registered it commits; never if
  it rolls back. `after_rollback(fn)` is the reverse.
- Registered inside a nested `context()`, it belongs to the outermost one. A `savepoint()` that
  rolls back drops what was registered inside it. Each `commit()` runs what was registered before
  it.
- A raising `fn` is logged and reported to error tracking, and the remaining ones still run. It
  MUST NOT raise to the caller: the commit is final, and an exception would tell the caller it
  was not.

### 9. The passthrough, in one place

| Need | Door | What is kept |
|---|---|---|
| pool, driver, connect arguments | `Database(url, **engine_options)` | everything |
| an option on this transaction | `context(engine={…})` | everything |
| a query the criteria cannot say | `statement()` → `run()` | the envelope, tracing |
| anything SQLAlchemy has | `unit.session` | the transaction, stamping, tracing — **not** hooks, `version` or cascades |

### 10. The port and the double

Features type the concrete `Repository` ([manifesto](../persistence/manifesto.md) rule 1). Each
new word is put to the manifesto's three questions (rule 3) — same meaning in a relational store,
a document store and memory; implemented or refused by `MemoryRepository`; the same behaviour
for a Feature on every store that accepts it:

| Word | Same meaning everywhere | `MemoryRepository` | Goes to |
|---|---|---|---|
| owned children on `save`/`remove` (§2) | yes — a document store embeds them | implements | the port, as part of what `save` means |
| `nowait` (§4) | yes, where a lock exists | refuses, as `for_update` | the port, beside `for_update` |
| exceptions (§5) | yes — every store has conflicts | raises them | the port's vocabulary |
| `upsert` (§6) | yes — upsert, `PutItem`, a dictionary | implements | the port |
| `update_all`, `remove_all` (§7) | yes — `updateMany`, a filter over a dictionary | implements | the port, if open question 5 keeps them |
| `context()` options, `after_commit`, `savepoint`, `commit` (§3, §8) | no — they describe a transaction not every store has | — | the SQLAlchemy `Repository` only |
| `context(engine=…)`, `statement()`, `session` (§9) | no — SQLAlchemy as it is | — | the SQLAlchemy `Repository` only |

The concrete class inherits the port, so the type checker holds every override to the port's
signature (manifesto rule 4).

### 11. Defaults

| Setting | Default |
|---|---|
| inferred to-many | owned |
| orphan with `NOT NULL` key / nullable key | deleted / key set to `NULL` |
| partial collection on save | children untouched, warning |
| `isolation`, `read_only`, `timeout` | the database's own, `False`, none |
| `retrying(on=…)` | `(StaleAggregate, TransactionConflict)` |
| `upsert(update=…)` | every column but the key and the identity |

### 12. Conformance

Tests per section, in `tests/orm`, against SQLite and against Postgres where the dialect differs
(isolation, `nowait`, SQLSTATE). For §2: a rebuilt collection leaves no stale row; an untouched
relation keeps its children; a partial one is left alone and warned about; a declared detach sets
to `NULL`; `remove` settles the children; the cascade issues a counted number of statements.

### 13. Maturity — capability by capability

| Capability | Where it comes from | Status |
|---|---|---|
| Optimistic lock (`version` → `StaleAggregate`) | JPA `@Version`, EF concurrency tokens | built |
| `for_update`, `skip_locked` | Django, SQLAlchemy | built |
| `nowait`, `LockNotAvailable` | Django `nowait`, JPA lock timeout | §4 |
| Savepoints, batched commits, `retrying` | SQLAlchemy `begin_nested`, Django `atomic` | built |
| Owned children saved and removed with the root | EF owned types, JPA `orphanRemoval`, Rails `dependent` | §2 |
| Foreign key to a business key | — | built (#133) |
| Isolation, read-only, timeout per unit of work | Spring `@Transactional`, Rails `transaction(isolation:)` | §3 |
| Engine errors named by what happened | Django `IntegrityError` split by backend, Spring `DataAccessException` hierarchy | §5 |
| Upsert | Django `update_conflicts`, Rails `upsert_all`, Ecto `on_conflict` | §6 |
| Writes by criteria | EF `ExecuteUpdate`, Django `update()`, Rails `update_all` | §7 |
| After-commit per transaction | Django `on_commit`, Spring `afterCommit`, Rails `after_commit` | §8 |
| Global session events | SQLAlchemy events | built (`Database.before_flush` …) |

**Not planned**, and why: **Active Record** (the persistence skill and `decisions.md` §16 — the
class never knows its table, and an entity that saves itself hides the transaction); **a tracking unit of work** that
writes on commit without `save` (#134 — `save` stays the one explicit door, and with §2 it already
writes the whole aggregate); **two-phase commit** across databases (one transaction per
database; the outbox pattern across them); **read replicas** (two `Database` objects and two
repositories, wired by the project); **an outbox in the framework** (`decisions.md` — durability
is the project's, and §8 is the hook it needs).

## Decisions

### Owned by default

The framework is not in production yet, so the default can be the one an aggregate means: a
to-many tied by a foreign key is part of its root unless declared otherwise. The table already
says how dependent the child is — a `NOT NULL` key cannot outlive its parent — which is the rule
EF Core settled on for required and optional relationships.

### Safe before complete

The cascade acts only on what it can see whole. An untouched relation is not an empty one, and a
page is not the collection: deleting from either would remove rows nobody asked to remove. Both
are left alone, the second with a warning, because a silent delete is the one outcome that
cannot be taken back.

### Upsert is its own verb

`save` is not a merge (its docstring): a record built by hand with an existing id is a
duplicate. An upsert overwrites without a version check and cannot tell create from update, so
it gets its own name and its own documented promises instead of a flag that changes what `save`
guarantees.

### Writes by criteria, against `decisions.md` §12

§12 rejected them because a generic write skips the aggregate's rules — and it does. They are
specified anyway because the cases they serve have no aggregate rule to skip: voiding a batch,
purging expired sessions, re-stamping a column after a migration. Today those cases drop to
`unit.session` and skip the rules *and* the scope, the `version` raise and the stamping. A named
door that keeps those three is the smaller risk. If the rejection stands, §7 is dropped and the
passthrough remains the answer.

### A refused word over an ignored one

An isolation level the dialect lacks would let a Feature pass on SQLite and lose a race on
Postgres — the same reason `for_update` is refused by the double. A timeout the dialect lacks
only costs time, so it warns.

## Phases

1. **Owned relations** (§2) — built: `cascade.py`, `Held`, `tests/data_layer/orm/test_cascade.py`.
2. **Transactions** (§3, §4, §5) — built: `context(...)`, `nowait`, `engine_errors.py`,
   `tests/data_layer/orm/test_transactions.py`.
3. **Bulk and hooks** (§6, §7, §8) — built: `upsert`, `update_all`/`remove_all`,
   `transaction_hooks.py`, `tests/data_layer/orm/test_bulk_writes.py`; the event log of #131 builds on §8.

Every engine-sensitive test runs on SQLite and, with `SINCPRO_POSTGRES_URL` set, on Postgres
(`tests/data_layer/orm/engines.py`).

## Open questions

1. ~~Orphans by key nullability, or always deleted?~~ *Neither: refused until the relation
   declares `Orphans.DELETE` or `Orphans.DETACH`.*
2. **Owned children loaded with the root.** Should `get`/`search` bring owned relations without a
   specification — one batched query per relation — as #132 asks? Inside `context()` they already
   resolve on first touch.
3. **`archive(root)`** — archive the owned children too, or leave them? *Built as: left.*
4. **`many_to_many` pairs** — should a touched many-to-many rewrite its rows in the table in
   between on save?
5. ~~Writes by criteria (§7).~~ *Reversed and built (decisions §22).*
6. ~~The commit writes unsaved changes.~~ *Only what was saved, by default; `writes=Writes.CHANGED`
   as the alternative (decisions §25).*
