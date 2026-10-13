# Extensibility specification

How a module that is not in this repository adds to an entity it does not own, how the core
releases new versions without destroying what was added, and what is built, proposed or missing.
Updated in place with every change ([how a specification is kept](README.md)).

**Status:** ✅ built and held by a test · 🟡 works with a workaround · ⬜ not built — a proposal.
Every ✅ in this page is held by a test in `tests/data_layer/orm/` —
`test_extending_an_entity.py`, `test_extension_types.py`, `test_consolidated_entity.py` and
`test_shared_table_migrations.py` — which run on SQLite and, with `SINCPRO_POSTGRES_URL`, on PostgreSQL.

## 1. The rule

> **What an extension adds, the extension owns.** Its own table, its own migrations, its own
> release. It names the core only by the opaque `id`, and the core never names it at all.

Everything else follows from that rule:

- A release of the core cannot destroy an extension's data, because the core does not own it.
- An extension cannot break the core, because it never alters a core table — unless it chooses
  the shared-table mode, which is allowed and costs what [§8](#8-when-a-release-comes) says.
- Uninstalling an extension is dropping its tables.
- Two extensions of one entity cannot collide, because they share nothing.

The alternative, as far as we know the one Odoo takes, is to merge every module into one class and
one table: reads are the fastest there is, and every module's migration is a migration of the
core's. A major
release then collides with all of them. We choose independence over that speed, and the
[measurements](#7-measured) say how much it costs.

## 2. Vocabulary

| Word | Means |
|---|---|
| **Core** | The modules Sincpro ships: base, sales, inventory, accounting. Sealed: no one edits it |
| **Module / extension** | Code in another repository that adds to the core. It never edits a core file |
| **Context** | A bounded context: its own bus, its own database or schema, its own migrations |
| **Published surface** | What the core promises to keep within a major version: an entity's fields and `id`, its events, its queries |

## 3. What an extension may do to an entity

| It may | Status | How |
|---|---|---|
| Add data about an existing record | ✅ | A 1:1 table keyed by the record's `id`, with the core class resolving it (`invoice.fiscal`) |
| Add several, from several modules | ✅ | Each its own table and its own relation name; they do not collide |
| Read it with the record, without a query per row | ✅ | A relation named in the specification: one more statement for the whole page |
| Add a value computed from the record | ✅ | An expression mapped from outside (`properties=`), filtered, ordered and grouped like a column |
| Keep light fields in a JSON slot the core reserves | ✅ | The same, over a `JSON` column; filter and group like a column |
| Keep the data in **another context** and read it by the bus | ✅ | `Relation.bus`: one call to the other bus per page, carrying the page's keys |
| Filter the entity by a field the other context owns | 🟡 | Ask that context for the ids, then `id in (…)`; a long list travels as one array on PostgreSQL |
| Add a behaviour when the entity is saved or read | ✅ | Hooks (`extends=`, `replaces=`, ordering), interceptors, `replaces=` of a use case |
| Add a new kind of the entity, with behaviour of its own | ✅ | A subclass with a table of its own (joined-table inheritance), in a chain of any depth |
| Add columns to the entity's **own table** | ✅ | A subclass given its parent's table; see [§5](#5-how-an-extension-is-declared-and-read) |
| Read the entity and get each row as the extension it has | ✅ | Downstream reading: the type is worked out from the rows, never stored |
| Read several sibling extensions as one flat record | ✅ | `Consolidated(Core, A, B)`, declared by a module that knows them all |
| Filter or group by a field of its own table | 🟡 | Only through a correlated subquery (15–30× slower) or a hand-written join |
| Reach the entity's own attributes from the extension (`delivery.move.total`) | ✅ | A back-reference; see below |
| Declare the cardinality of what it installs | 🟡 | Today it writes `Invoice.__annotations__[name]` itself — see [§6](#6-what-is-missing) |
| Change how a core field is shown (label, read-only, required) | ⬜ | An overlay on `Meta`, never an edit of the core's `presentation` |
| Remove, rename or retype a core field | ❌ | Never. The core is sealed; that is a major release of the core |

## 4. Which mechanism, when

| You need | Use | Why |
|---|---|---|
| A **concept of its own** with behaviour (distribution: routes, deliveries, vehicles), tightly bound to sales, inventory and manufacturing | A **bounded context** of its own | It owns its tables and learns about invoices by events and by id; the coupling is a contract, not a join |
| More **information about an existing record** (the fiscal data of an invoice) | A **1:1 extension table** | Typed, indexed, migrated by its owner, dropped on uninstall |
| **Light fields** a customer defines, with no deployment | The **JSON slot** | No join and no migration; the definition is data. Costs space if every row fills many keys |
| A **new kind** of the entity (a fiscal invoice with its own rules) | A **subclass** with a joined table | The type itself is different |
| A different **behaviour** on save or read | **Hooks and interceptors** | Behaviour is code, not data |
| Reuse a **behaviour across the core's own entities** (archive, audit, change tracking) | A **mixin**, inside the core | See below |

### Inheritance, mixins and extra tables

- **A mixin is how the core's own entities share behaviour** (`ArchivableMixin`, `AuditedMixin`,
  `ChangeTrackingMixin`). It is a design tool of the code that owns the class.
- **A mixin is not the way another repository extends an entity.** The core cannot inherit from a
  module it does not know, so the final class would have to be assembled by whoever deploys:
  `AccountMove(Core, SaleFields, ProjectFields)`. That puts the extension's columns in the core's
  table (the Odoo way), makes the order of the bases matter, and lets two modules mean the same
  name. The type checker also cannot follow a class assembled at start-up.
- **Composition is the way**: the extension is a class of its own with a table of its own, and the
  entity reaches it through a relation. `invoice.fiscal.cufd`, not `invoice.cufd`.
- **A subclass is for a different kind of record**, not for adding fields to the same kind.

### Using the entity's attributes from the extension

Having `MoveDelivery` in hand, can you use everything `AccountMove` has? There are three ways, tried
with the framework as it is:

| Way | You write | Status | What to know |
|---|---|---|---|
| **Back-reference** | `delivery.move.total` | ✅ | The extension names its core record by a relation; `move` is typed as `AccountMove`, so the IDE completes every attribute. One more statement per page. Works for any number of extensions |
| **Subclass** | `class DeliveryMove(AccountMove)` | ✅ | The object has every attribute **flat** (`m.number`, `m.total`, `m.route`). But it is a *kind* of move, not an addition to one: a move the core created is a `DeliveryMove` only once its extension row exists, and two subclass extensions are two kinds, **never one object** |
| **Combined view** | `MoveConsolidated` with the fields of the core and of every extension | ✅ | A class of its own, mapped to the outer join of their tables with `Consolidated(Move, MoveDelivery, MoveFiscal)`. It does not inherit the entities (a class that inherits two mapped entities cannot be instantiated), so siblings can be one object. Reads and writes every table, the core's first |

Tests: `test_an_extension_reaches_every_attribute_of_the_entity_through_a_back_reference`,
`test_an_extension_declared_as_a_subclass_has_every_attribute_of_the_entity_flat`,
`test_a_record_the_core_created_becomes_the_extended_kind_once_its_row_exists`,
`test_two_subclass_extensions_of_one_entity_are_two_kinds_and_never_one_object`.

**How to choose.** A chain of modules, each extending the one before: subclasses, flat and typed.
Several independent modules adding *information* to the same entity: back-reference and
composition. A screen that wants one flat object of siblings: the consolidated record, declared in a
module that depends on all of them. The type checker follows every one of these because each is an
ordinary class; none of it is assembled by name at start-up.

## 5. How an extension is declared and read

An extension is a class and a table. What decides where its columns live is the table it is given,
and nothing else changes for the module that writes it.

| Mode | Declare | Read as | Its migration |
|---|---|---|---|
| **A table of its own, flat** | `class MoveDelivery(Move)` and `map_aggregates(reg, {MoveDelivery: delivery_t})`; the table's `id` references the parent's | The class, with every field of the parent: `move.number`, `move.route` | Creates its table, in its own chain |
| **A table of its own, delegated** | A class of its own and `relations={Move: {"delivery": Relation.foreign_key(…)}}` | Nested: `move.delivery.route`, named in the specification | The same |
| **The parent's own table** | The same subclass, given the parent's table: `{MoveZone: move_t}` | The class, flat. A row *has* the extension when any column the extension owns holds a value | Adds columns to a table that is not its own; see [§8](#8-when-a-release-comes) |
| **A JSON slot** | `properties=` over the core's `JSON` column | A field, filtered and grouped like a column | None |
| **Another context** | `Relation.bus(…)` | Nested, one call per page | Its own context's |

The shared-table mode is allowed and not preferred: it makes two owners migrate one table, and the
row cannot tell an extension whose columns are all empty from no extension at all.

### The type of a row

Reading an entity answers **downstream**: each row comes back as the most derived extension it has.

```python
store.search(Move, Criteria())
# [Move, MoveDelivery, MoveTracking]      A has nothing added, B a delivery, C a delivery and a tracking
store.get(Move, "C").carrier              # "DHL": the row is a MoveTracking
```

- **Nothing stores which class a row is.** The type is a SQL expression the mapper reads as the
  discriminator of the root; it is written when a statement is compiled, from the extensions
  registered by then. A module installed after the core was mapped is part of the answer, and the
  statements compiled before it are discarded (`test_an_extension_installed_after_the_core_was_mapped_is_part_of_the_answer`).
- **One more statement per type found**, not a join per extension: the columns of an extension are
  read for the rows that have it. A page of fifty moves with no extension reads one statement.
- **Reading an extension decides among itself and what extends it.** `search(MoveDelivery)` returns
  deliveries and trackings, each as what it is; a sibling that also has a row is another kind of
  the same record, not a level below this one.
- **When two siblings both have a row** the core answers the one installed first; each of them,
  read by its own class, answers itself (`test_two_subclass_extensions_of_one_entity_are_two_kinds_and_never_one_object`).
- **`strict`** — `map_aggregates(reg, tables, strict=[Move])` — makes the core answer only itself,
  whatever extensions exist. An extension in the parent's own table cannot live under a strict core,
  and is refused at start-up saying so.
- The core filters, orders and groups by its own fields exactly as before
  (`test_the_core_filters_by_its_own_fields_whatever_the_extensions_are`).

Held by `tests/data_layer/orm/test_extension_types.py`: the chain, the late install, the levels
below, one save writing every level in one transaction, the strict core, the shared table and its
two refusals. They run on SQLite and PostgreSQL.

### The consolidated record

```python
@dataclass
class MoveConsolidated(Entity):          # in a module that depends on delivery and fiscal
    number: str = ""
    route: str = ""
    cufd: str = ""

map_aggregates(reg, {MoveConsolidated: Consolidated(Move, MoveDelivery, MoveFiscal)})
```

The class is mapped to `move LEFT JOIN move_delivery LEFT JOIN move_fiscal`: a record without a row
of an extension reads that extension's fields as their defaults, and the criteria filters by a field
of any of them. Saving writes every table, the core's first; editing updates each table where its
field lives. Two extensions that name a field alike are refused, naming both tables; `id`,
`created_at`, `updated_at` and `version` are written to each. Held by
`tests/data_layer/orm/test_consolidated_entity.py`.

Not covered: editing a consolidated record whose extension row does not exist yet (the update finds
no row), and deleting one (which rows go).

## 6. What is missing

| Piece | Status | What it needs |
|---|---|---|
| Declared cardinality of an installed relation | ⬜ | `Relation` says to-one or to-many; today the framework reads it from an annotation the extension cannot write |
| A join generated to filter or group by an extension's field | ⬜ | The translator joins the extension's table when the criteria names it |
| The flat shape of a delegated extension on the wire | ⬜ | Today a relation answers nested under its name, with the id and the audit columns repeated; the schema and the client do not describe it |
| A request that says which extensions to bring (`only`, a named view) and a default per entity | ⬜ | The specification already narrows; the extensions are not yet part of it |
| Create and Edit that split a flat payload by the owner of each field | ⬜ | Saving the deepest class or a consolidated record already writes every table; the command that receives a flat JSON does not |
| A reserved JSON slot on extensible entities | ⬜ | The core ships a `properties` column on the entities it marks extensible, from the first release: adding it later is a migration of a large table |
| Declared dependency of an extension on core fields | ⬜ | `uses=("total",)`, checked against the core's `Meta` when the module loads |
| Contract version of an entity and a loader that checks it | ⬜ | The extension declares the core range it supports; the loader refuses to start outside it |
| Compatibility kit | ⬜ | A test class the extension runs in its own CI against a core that is not installed yet, like `ComputedFieldsContract` |
| Overlay of presentation on `Meta` | ⬜ | Label, read-only and required for a core field, from a module |
| Fields a customer defines at run time | ⬜ | The definition as data (name, type, label); the slot holds the values |

Not built on purpose: an accessor `move.ext(Type)` and generated stubs for what is installed. A
class that is read by its own type already gives the type checker everything, and a chain of
subclasses or a consolidated record give it one flat object when a screen needs one.

## 7. Measured

PostgreSQL 17, 1,000,000 invoices, extensions of 4 fields each, median of 5, warm cache, one
machine. The strategies: **wide** is every extension field a column of the core table (Odoo's way);
**side** is one table per extension joined on `id`; **JSON** is one JSONB column on the core table;
**subquery** is a side table read as a correlated scalar subquery per field.

| | wide | **side tables** | JSON | subquery |
|---|---|---|---|---|
| page of 50 with all fields, 10 extensions | 0.8 ms | **7.7 ms** | 0.6 ms | 2.8 ms |
| same page, extensions not asked for | 0.3 ms | **0.3 ms** | 0.2 ms | — |
| page of 50 filtered by one extension field | 0.7 ms | 2.8 ms | 0.7 ms | 27 ms |
| group all rows by one extension field | 71 ms | 136 ms | 134 ms | 885 ms |
| group all rows by three extension fields | 71 ms | 339–397 ms | 252 ms | 1,766 ms |
| one invoice by id with all fields | 0.3 ms | 0.8 ms | 0.3 ms | — |
| single-invoice transactions per second | 1,276 | **598** | 1,461 | — |
| 100,000 invoices in one statement | 0.5 s | 1.2 s | 1.8 s | — |
| table size | 419 MB | 1.0 GB | 1.1 GB | — |

- **Side tables are fine for transactional use.** PostgreSQL removes a `LEFT JOIN` to a unique key
  whose columns are not read, so listing invoices does not pay for extensions it did not ask for.
  Asking for the ten extensions' fields costs about 8 ms a page.
- **They cost on analysis across the whole table**: two to five times wide columns when grouping by
  their fields. A report that groups by several extension fields over millions of rows should read a
  projection, not the join.
- **Writing is about twice as slow** with ten extensions: eleven inserts in one transaction.
- **A JSON slot costs space** when every row carries every key (1.1 GB against 419 MB): the key
  names repeat in each row. Sparse fields cost little.
- **Never read a side table through a correlated subquery** (`column_property(select(...))`).
- Not measured: ten times the rows, concurrent writers, a cold cache, partitioned tables.

## 8. When a release comes

### The core releases

| The core… | Effect on an extension | Held by |
|---|---|---|
| fixes a bug (patch) | None | — |
| **adds a field, a table or an event (minor)** | None. The extension keeps working and sees the new field | `test_a_minor_release_that_adds_a_field_leaves_the_extension_untouched` |
| **renames or moves a field (major)** | A table that names only the `id` is untouched. An **expression** the extension wrote over the old column stops being buildable; the extension would find out as it starts, from SQLAlchemy (not held by a test: the framework does not check it, which is why `uses=` is proposed) | `test_a_major_release_that_renames_a_field_does_not_reach_a_table_that_names_only_the_id` |
| removes an entity (major) | An extension that imports it fails at import. Not held by a test | — |
| changes the type of `id` | **Forbidden.** The `id` is opaque text; it is what makes the rule work | — |

How the core makes a major survivable:

1. **Expand, then contract.** To rename `total` to `amount`: one release adds `amount` and writes
   both; the next marks `total` deprecated in `Meta`; the major after drops it. Nothing an extension
   could have read vanishes in the same release that announces its replacement.
2. **The published surface of an entity is versioned.** Adding is free; removing or renaming is a
   major, with a deprecation period and a note of what changes.
3. **The extension declares what it reads** (`uses=`) and the core range it supports, and the
   loader checks both when the application starts — ⬜, see [§6](#6-what-is-missing).
4. **The kit** lets the extension's own CI say, against the new core, whether it still works,
   before anyone upgrades — ⬜.

### The extension releases

| The extension… | Effect on the core |
|---|---|
| adds a column to its table | None. Its own migration, on its own branch of the timeline |
| is installed | Creates its tables. They reference the core's `id`, so the core's tables exist first |
| is **uninstalled** | Drops its tables. The core does not notice (`test_an_extension_is_uninstalled_by_dropping_its_table_and_the_core_does_not_notice`) |
| is released in a version that needs a newer core | The loader refuses to start — ⬜ |

**A table two chains declare.** An extension that adds a column to the core's table has its own
chain, and autogenerate compares a chain with the tables it declares: the extension declares
`move(id, zone)` and would drop `number` and `total`; the core's next step would drop `zone`. On
PostgreSQL that was measured, not guessed — a reviewed step that missed the form
`op.drop_column('move', 'number')` left the table with `(id, zone)`. `Migrations` therefore tells
every Alembic engine on a database who else migrates beside it, and each chain leaves alone a column
of a shared table that another chain declares and it does not. A column nobody else declares is
still dropped by the chain that owns it, and `check` reports no drift for what the other declares.
Held by `tests/data_layer/orm/test_shared_table_migrations.py`, on SQLite and PostgreSQL.

When an extension shares the core's database it may declare the key as a real foreign key with
`ON DELETE CASCADE`, so deleting an invoice deletes its extension rows
(`test_a_core_row_that_is_deleted_takes_the_extension_row_with_it_when_they_share_a_database`, on
PostgreSQL). Across contexts there is no foreign key: the other context keeps its own copy of what
it needs and reacts to events.

## 9. Across bounded contexts

- **Extend inside a context; replicate across contexts.** A module that is a concept of its own
  becomes a context with its own tables; the same extension mechanism serves a context that wants to
  add to its own copy of someone else's entity.
- **Reading** another context's data is a relation by its bus: one call per page
  (`test_data_that_lives_in_another_context_is_resolved_by_its_bus_one_call_per_page`).
- **Filtering** by it is two steps: the ids, then `id in (…)`
  (`test_invoices_are_filtered_by_a_field_the_other_context_owns_by_asking_it_for_the_ids`).
  The list can be large: it travels as one parameter on PostgreSQL.
- **What must not change after the fact is copied**, not referenced: an invoice keeps the name and
  the tax id of its customer as they were when it was issued.
- **No foreign key and no join across contexts.** What crosses is a fact, with an outbox and an
  idempotent consumer.

## 10. A module today

What works with the framework as it is, written the long way (the cardinality annotation is the
workaround of [§6](#6-what-is-missing)):

```python
from dataclasses import dataclass

from sqlalchemy import Column, ForeignKeyConstraint, Text

from sincpro_framework.data_layer.orm import Relation, map_aggregates
from sincpro_framework.data_layer.orm.sqlalchemy.entrypoint.template_table import entity_table
from sincpro_framework.ddd import Entity


@dataclass
class InvoiceFiscal(Entity):
    cufd: str = ""
    control_code: str = ""


def install(registry, invoice_class, invoice_table):
    table = entity_table("invoice_fiscal", registry.metadata,
                         Column("cufd", Text), Column("control_code", Text))
    table.append_constraint(
        ForeignKeyConstraint(["id"], [invoice_table.c.id], ondelete="CASCADE"))

    invoice_class.__annotations__["fiscal"] = InvoiceFiscal | None
    map_aggregates(
        registry,
        {InvoiceFiscal: table},
        relations={invoice_class: {
            "fiscal": Relation.foreign_key(InvoiceFiscal, parent_field="id", related_field="id")}},
    )
```

```python
from sincpro_framework.ddd import Criteria
from sincpro_framework.ddd.criteria import Specification

page = repository.search(Invoice, Criteria(specification=Specification({"fiscal": Criteria()})))
page[0].fiscal.cufd
```

The type checker does not see `Invoice.fiscal`, because it is installed at run time. That is the
cost of the long way, and what the stubs of [§6](#6-what-is-missing) remove.

## 11. Decisions pending

| # | Question | Leaning |
|---|---|---|
| 1 | Does the core reserve `properties` on **every** entity or only on those it marks extensible? | Only the marked ones: a table nobody extends should not carry it |
| 2 | May a **customer** define fields at run time, or only a module's developer? | Both, with the definition stored as data |
| 3 | Do the lines of the ledger, immutable once posted, keep extension fields in the slot or in a side table? | The slot: it avoids a second write per line |
| 4 | When an extension has a row, should reading the core also bring its fields without being asked? | Yes by default for a single record; a list is configured per entity, because a page with a thousand extensions is a thousand statements |
| 5 | What is the flat shape of a delegated extension on the wire, and who splits a flat payload on a write? | Flat by owner of each field in `Meta`; nested as an option |
| 6 | Does the loader enforce the core range, or only warn? | Enforce: a half-working ERP is worse than one that does not start |
