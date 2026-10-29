# Extensibility specification

How a module that is not in this repository adds to an entity it does not own, how the core
releases new versions without destroying what was added, and what is built, proposed or missing.
Updated in place with every change ([how a specification is kept](README.md)).

**Status:** ✅ built and held by a test · 🟡 works with a workaround · ⬜ not built — a proposal.
Every ✅ in this page is held by a test in `tests/data_layer/orm/` —
`test_extending_an_entity.py`, `test_extension_types.py`, `test_consolidated_entity.py` and
`test_shared_table_migrations.py` — which run on SQLite and, with `SINCPRO_POSTGRES_URL`, on PostgreSQL. The CI runs SQLite only; PostgreSQL is run locally before a release (file by file: the whole directory in one process already failed before these tests).

## Requirements

What this specification has to cover, as it was asked for, and where each one stands. The sections
below say how; the last column says what holds it.

| # | Requirement | Status | Held by |
|---|---|---|---|
| R1 | The core knows nothing of its extensions, and the commands it already has (`Get`, `Search`, `Edit`) keep working, unchanged, when a module is installed | ✅ | `test_the_same_get_of_the_core_returns_the_extension_flat_with_nothing_declared`, `test_a_new_record_with_the_fields_of_an_extension_is_created_as_that_extension` |
| R2 | An extension owns its table, its migrations and its release; a release of the core does not destroy what it holds | ✅ | [§1](#1-the-rule), [§8](#8-when-a-release-comes) |
| R3 | The storage is the module's choice: a table of its own, the entity's own table, a JSON slot, or another context | ✅ | [§5](#5-how-an-extension-is-declared-and-read) |
| R4 | The same extension is a flat typed object (a chain), one flat record of several (siblings), or a nested part of the answer | ✅ | `test_extension_types.py`, `test_consolidated_entity.py`, `test_an_answer_nests_each_extension_under_its_key_when_the_entity_delegates` |
| R5 | Reading the core brings every extension by default and can be narrowed: to the core alone, or to the fields asked for | ✅ | `strict`, the specification; [§5](#whose-a-field-is-and-what-a-client-is-told) |
| R6 | Writing creates the core first and then each extension, in one transaction, through one repository per context | ✅ | `test_one_save_of_the_deepest_class_writes_every_level_in_one_transaction`, `test_extension_fields.py` |
| R7 | The type checker follows it with no tool of its own: classes, not generated code, plugins or stubs | ✅ | [§6](#6-what-is-missing) (what was not built, on purpose) |
| R8 | The migrations of the core and of a module run as independent chains, on a table they share too | ✅ | `test_shared_table_migrations.py` |
| R9 | A client is told what an entity has, whose each field is, and how the answer is shaped | ✅ for the contract, ⬜ for the TypeScript client | `Meta.owner`, the answer's schema; [§6](#6-what-is-missing) |
| R10 | A module declares what it reads and what it was written against, and the application does not start when that no longer holds | ✅ | `test_extension_contract.py` |
| R11 | The module proves it in its own CI | ✅ | `ExtensionContract` |
| R12 | The cost is known, and a core can make it constant | ✅ | [§7](#7-measured), [§5](#a-core-that-stores-the-type-of-its-rows) |
| R13 | A customer defines fields at run time | ⬜ | [§6](#6-what-is-missing): a component of its own |
| R14 | Code follows the framework's layers: the registry and the answer's shape in `ddd`, the mapper and the writes in `data_layer/orm/sqlalchemy/services`, `map_aggregates` a facade | ✅ | `tests/data_layer/orm/test_layers.py`, [§12](#12-where-it-lives) |

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
| Read it with the record, without a query per row | ✅ | A relation named in the specification: one more statement for the whole page. An extension that is a subclass needs no naming: the row comes back as the extension |
| Add a value computed from the record | ✅ | An expression mapped from outside (`properties=`), filtered, ordered and grouped like a column |
| Keep light fields in a JSON slot the core reserves | ✅ | `properties_column()` on the core's table, `slot_field(table, "zone")` in the module's `properties=`: a field filtered, grouped and measured like a column (`tests/data_layer/orm/test_properties_slot.py`) |
| Keep the data in **another context** and read it by the bus | ✅ | `TableRelation.bus`: one call to the other bus per page, carrying the page's keys |
| Filter the entity by a field the other context owns | 🟡 | Ask that context for the ids, then `id in (…)`; a long list travels as one array on PostgreSQL |
| Add a behaviour when the entity is saved or read | ✅ | Hooks (`extends=`, `replaces=`, ordering), interceptors, `replaces=` of a use case |
| Add a new kind of the entity, with behaviour of its own | ✅ | A subclass with a table of its own (joined-table inheritance), in a chain of any depth |
| Add columns to the entity's **own table** | ✅ | A subclass given its parent's table; see [§5](#5-how-an-extension-is-declared-and-read) |
| Read the entity and get each row as the extension it has | ✅ | Downstream reading: the type is worked out from the rows, never stored |
| Read several sibling extensions as one flat record | ✅ | `Consolidated(Core, A, B)`, declared by a module that knows them all |
| Filter, group or measure the core by a field of an extension | ✅ | The core's criteria names it (`Condition(field="route")`); it is read through a correlated subquery, which costs 15–30× a join on a report over millions of rows, so such a report reads the extension's class. Ordering by it is refused: a record may lack the extension, so the field is nullable |
| Reach the entity's own attributes from the extension (`delivery.move.total`) | ✅ | A back-reference; see below |
| Declare the cardinality of what it installs | ✅ | `TableRelation.foreign_key(…, many=False)`: the data mapper writes the annotation the module cannot (`test_a_relation_an_extension_installs_says_its_own_cardinality`) |
| Change how a core field is shown (label, help, read-only, required) | ✅ | `map_aggregates(…, overlay={Core: {field: {…}}})`: an overlay on `Meta`, never an edit of the core's `presentation`; anything else about the field is refused as the core's |
| Add values to a core's enum field, or name them differently | ✅ | The subclass redeclares the field with an enum of its own (`state: SiatState`) — Python refuses to subclass an enum that has members, so it is rewritten, ideally a superset of the core's. `describe` of the subclass publishes its values and labels; the core's stays as the core declared it; a query by the core or by the subclass reads the new values (`test_inheritance_of_an_enum_field.py`). A type checker flags the override (`reportIncompatibleVariableOverride`): the core's own code may meet a value it never knew, which is the subclass's to own |
| Say what it reads from the core and what it was written against | ✅ | `uses={Core: ("total",)}` and `requires={Core: "2.1"}`, checked when the module is mapped; see [§8](#8-when-a-release-comes) |
| Prove all of it in its own CI | ✅ | `ExtensionContract`, in `sincpro_framework.runtime.testing.contracts.extension` |
| Remove or rename a core field | ❌ | Never. The core's columns are the core's; that is a major release of the core. Redeclaring a field's type in a subclass is not a removal: see «Add values to a core's enum field» |

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
| **A table of its own, delegated** | A class of its own and `relations={Move: {"delivery": TableRelation.foreign_key(…)}}` | Nested: `move.delivery.route`, named in the specification | The same |
| **The parent's own table** | The same subclass, given the parent's table: `{MoveZone: move_t}` | The class, flat. A row *has* the extension when any column the extension owns holds a value | Adds columns to a table that is not its own; see [§8](#8-when-a-release-comes) |
| **A JSON slot** | `properties=` over the core's `JSON` column | A field, filtered and grouped like a column | None |
| **Another context** | `TableRelation.bus(…)` | Nested, one call per page | Its own context's |

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

### Whose a field is, and what a client is told

`ddd/entity/extensions.py` holds, per root entity, each extension, the fields it adds and the key it
nests under. `map_aggregates` fills it; everything that has to know whose a field is asks it.

- **`Meta`** lists the fields of every extension on the core, each with `owner` (`"MoveDelivery"`),
  nullable and not sortable (`test_the_definition_of_the_core_says_whose_each_field_is`). The
  extension's own definition has its fields with no owner.
- **The answer's schema** — what OpenAPI and a generated client are built from — describes the
  extensions' fields too: flat and optional, or as one nullable object per extension
  (`test_the_schema_of_an_answer_describes_the_fields_of_the_extensions`).
- **Flat by default.** A record is written as the class it is, so a `Get` or a `Search` written for
  the core answers `route` the day a module installs `MoveDelivery`, with nothing declared
  (`test_the_same_get_of_the_core_returns_the_extension_flat_with_nothing_declared`).
- **Delegated by choice.** `map_aggregates(reg, tables, delegated=[Move])` writes each extension's
  fields under its key — `MoveDelivery` under `Move` is `delivery` — and an extension the record does
  not have is `null`, so the shape does not move with the row
  (`test_an_answer_nests_each_extension_under_its_key_when_the_entity_delegates`).
- **Only what is wanted.** A specification that names a field of an extension brings it; one that
  does not, leaves it out. The core alone is a specification of the core's fields, and the entity's
  default reading (`DEFAULT_READING`) may name any of them
  (`test_a_specification_that_names_a_field_of_an_extension_brings_it`). The store still reads which
  extension each row has; a core that must not pay for that answers only itself (`strict`).

### Delegated answers: what they are for

**The intent.** A screen or a client that shows an entity extended by several modules wants the
entity's own fields at the top and what each module added under that module's own key. Then two
modules can never collide on a field name, a client can ignore a module it does not know, and
uninstalling a module removes one key and not fields scattered through the record. Flat is the
default because it needs nothing from the client; delegated is for the client that groups by module.

**It is a shape, not a storage mode.** It does not depend on where the extension keeps its columns
(a table of its own, the entity's table): only on how an answer is written and how a form is read.
It is chosen by the entity, for all of its extensions: `map_aggregates(reg, tables, delegated=[Move])`.

```
flat                                              delegated
{"id": "C", "number": "F-C",                      {"id": "C", "number": "F-C",
 "route": "R7", "carrier": "DHL"}                  "delivery": {"route": "R7"},
                                                   "tracking": {"carrier": "DHL"},
                                                   "fiscal": null}
```

| Rule | Why | Held by |
|---|---|---|
| The key of an extension is its class name without the root's (`MoveDelivery` under `Move` is `delivery`), in snake case | Nothing for a module to declare; readable | `test_an_answer_nests_each_extension_under_its_key_when_the_entity_delegates` |
| Every extension is a sibling key of the root, a chain included: `tracking` is not inside `delivery` | A chain is one record with several parts; the client reads parts, not a tree | the same |
| An extension the record does not have is `null` | The shape does not move with the row | the same |
| A specification that names none of an extension's fields leaves its key out; one that names a field brings its key with that field | The same mask as flat, so a client narrows the same way | `test_a_specification_that_names_a_field_of_an_extension_brings_it` |
| The schema describes each key as an object or `null`, with the extension's fields | A generated client types it | `test_the_schema_of_an_answer_describes_the_fields_of_the_extensions` |
| `Edit` takes the record back as it was read: `{"delivery": {"route": "R9"}}` | A form reads and writes one shape | `test_a_form_that_read_a_delegated_record_sends_it_back_as_it_read_it` |
| An extension sent as `null` writes nothing; flat values are still taken as they are | A form sends the whole record; a client that never groups still works | `test_an_extension_the_record_does_not_have_writes_nothing_when_the_form_sends_it_null`, `test_a_delegated_entity_takes_flat_values_as_they_are` |
| A nested value with a field the extension does not have is refused, naming it | A typo is not silently dropped | `test_a_nested_value_with_a_field_the_extension_does_not_have_is_refused_naming_it` |
| An entity that does not delegate does not take its extensions nested | The shape is the entity's choice | `test_an_entity_that_does_not_delegate_does_not_take_its_extensions_nested` |
| `changed` may name `delivery` (all its fields), `delivery.route` or `route` | The derivations a form reports run in any of the shapes | `test_the_fields_a_form_reports_changed_are_taken_nested_or_flat` |
| The answer to an `Edit` is nested too | A form never sees two shapes | `test_the_answer_to_an_edit_nests_the_extensions_when_the_entity_delegates` |
| Two extensions that would nest under one key are refused, naming both | Two modules with a `MoveDelivery` would otherwise overwrite each other | `test_two_extensions_that_would_nest_under_one_key_are_refused_naming_both` |

**Where it is, to change it.** The choice is `extensions.DELEGATED` (`ddd/entity/extensions.py`),
with the key computed by `key_of` when an extension is registered. The answer is written by
`_delegated` in `ddd/query.py`, after the record is cut by the specification; its schema is
`_extended`, in the same file. A form is read back by `EntityEdits.flattened` and `nested_names`,
in `ddd/editing/edits.py`, which turn the nested values into the flat ones the rest of `Edit`
already takes. Nothing in the data layer knows about it.

**What it does not do, and how it could grow.**

- It is all or nothing per entity. Choosing per extension would need the choice to live on the
  extension (`map_aggregates` has nowhere to say it today).
- The key cannot be named; it is derived. A module that wants another word needs a place to say it.
- An extension installed as a relation (`TableRelation.foreign_key` on a side table) is not part of
  this: it is already one named relation, nested under its name when the specification names it.
- Not every client needs it. The TypeScript client has not been taught either shape.

### Creating and editing through the core's commands

`Edit`, written once for the core, takes the fields of every extension installed:

- A **new record** whose values carry a field of an extension is created as that extension — the
  deepest class of a chain that holds them all (`test_a_new_record_with_the_fields_of_an_extension_is_created_as_that_extension`).
- A **stored record without the extension**, edited with its fields, is given the rows first
  (`Repository.extend`, a capability of its own: the levels between its class and the extension,
  each with its fields' defaults) and then saved as the extension; the core's row is not touched
  (`test_a_stored_record_without_the_extension_is_given_it_by_an_edit_that_names_its_fields`).
- Fields of **siblings** that no class holds are refused naming them: send them in separate edits,
  or map a consolidated record for the screen that needs them together
  (`test_fields_of_sibling_extensions_that_no_class_holds_are_refused_naming_them`).
- A draft keeps the extension's fields like any other and stores nothing.

### Removing

Removing a record read as an extension removes the rows of every level. Removing a plain instance of
the core — one that came from a core that answers only itself — removes the rows of its extensions
first, deepest first (`test_removing_a_plain_core_instance_takes_the_rows_of_its_extensions_with_it`,
PostgreSQL included: it is the order its foreign keys demand).

### A core that stores the type of its rows

Working the type out asks every extension's table for each row it reads, so the cost is linear in
the extensions installed. A core whose table declares a column named `extension_type` stores it
instead: the framework writes the class a record is when it is inserted and when an extension is
given to it (`extend`), and a read takes the column. Reading a page of 50 takes 3.4 ms with 200
extensions installed, against 22.6 ms working it out and 4.8 ms with none.

- The column is the core's choice, nullable, and not a field of the entity; a row with no value is
  the core, and a value of a class no longer installed reads as the level that is being read.
- Rows an extension wrote outside the framework, or rows that predate the column,
  are typed by `reclassify(Core)`, one statement that writes every row's type from the extension
  rows that exist.
- An extension in the core's own table is still recognised by its columns; the stored type serves
  the extensions with a table of their own.

Held by the last tests of `tests/data_layer/orm/test_extension_types.py`.

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

Editing a consolidated record that has no row of an extension, and changes a field of it, gives it
the row first, holding what the record says (`test_a_consolidated_record_without_a_row_of_an_extension_is_edited_by_giving_it_the_row`);
removing one removes the rows it has (`test_removing_a_consolidated_record_without_all_its_rows_removes_the_ones_it_has`).

### Where it breaks, measured

| Situation | What happens | Held by |
|---|---|---|
| A core release adds a field an extension already has | Refused where the extension is mapped, naming both tables. Before, SQLAlchemy joined the two columns under one attribute and a save wrote the extension's value over the core's | `test_a_core_release_that_adds_a_field_an_extension_has_is_refused_naming_both_tables` |
| Many extensions on one core | The type expression asks every extension's table for each row it reads, so the cost is linear (a core that stores the type, [§5](#a-core-that-stores-the-type-of-its-rows), makes it constant): on PostgreSQL, 100,000 rows, a page of 50 takes 4.8 ms with none, 7.4 ms with 10, 9.8 ms with 50 and 22 ms with 200; a read by id 1.6, 3.6, 6.4 and 16 ms. Aggregations do not pay it: the expression is not read | measured, not held |
| Ordering the core by a field of an extension | Refused, naming the nullable field | `test_a_field_of_an_extension_is_not_a_field_to_order_by_because_a_record_may_lack_it` |

## 6. What is missing

| Piece | Status | What it needs |
|---|---|---|
| A join instead of a subquery to filter or group the core by an extension's field | ⬜ on purpose | A correlated subquery is the one form that holds in a `WHERE`, a `HAVING`, a bulk update and a count alike (nineteen statements are built from one clause); a report over millions of rows reads a consolidated record, which is a join |
| Create or edit sibling extensions in one command | ⬜ on purpose | Refused, naming them: no class holds fields of two siblings. A consolidated record, or two edits |
| Fields a customer defines at run time | ⬜ | A component of its own: the definition stored as data (name, type, label) and the slot holding the values. Not started |
| The TypeScript client reading `owner` and the extension fields | ⬜ | The contract carries them, and the package's `FieldMeta` needs `owner?: string`; the package was not touched |

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

**What a module says, and what the framework checks.** The core publishes a version on an entity
(`CONTRACT = "2.3"`: a major that breaks, a minor that adds). A module declares the one it was
written against and the fields it reads when it is mapped:

```python
map_aggregates(registry, {MoveDelivery: delivery_t},
               requires={Move: "2.1"}, uses={Move: ("total",)})
```

The same major and at least that minor is a core it works with. Anything else stops the application
from starting, naming the entity and both versions; so does a field the core no longer has, naming it.
Held by `tests/data_layer/orm/test_extension_contract.py`. The module's own CI runs
`ExtensionContract` against the core it will be installed on, before anyone upgrades.

How the core makes a major survivable:

1. **Expand, then contract.** To rename `total` to `amount`: one release adds `amount` and writes
   both; the next marks `total` deprecated in `Meta`; the major after drops it. Nothing an extension
   could have read vanishes in the same release that announces its replacement.
2. **The published surface of an entity is versioned.** Adding is free; removing or renaming is a
   major, with a deprecation period and a note of what changes.
3. **The extension declares what it reads** (`uses=`) and the core range it supports, and the
   loader checks both when the application starts — ✅.
4. **The kit** lets the extension's own CI say, against the new core, whether it still works,
   before anyone upgrades — ✅ (`ExtensionContract`).

### The extension releases

| The extension… | Effect on the core |
|---|---|
| adds a column to its table | None. Its own migration, on its own branch of the timeline |
| is installed | Creates its tables. They reference the core's `id`, so the core's tables exist first |
| is **uninstalled** | Drops its tables. The core does not notice (`test_an_extension_is_uninstalled_by_dropping_its_table_and_the_core_does_not_notice`) |
| is released in a version that needs a newer core | The application does not start, naming both versions (`requires`) |

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
- **No foreign key and no join across contexts.** What crosses is a fact, published once the
  write committed, and an idempotent consumer.

## 10. A module today

What a module that adds a table of its own and reaches it from the core's record writes. The
extension is a plain dataclass, **not an `Entity`**: it takes the invoice's `id`, and the dates and
version stay the invoice's, so the answer does not repeat them (`{"fiscal": {"id": …, "cufd": …}}`).
The relation says its own cardinality, so nothing is written on a class the module does not own.
A module that wants an entity with a life of its own — its own dates and version — makes an `Entity`
of it, and then it carries its own.

```python
from dataclasses import dataclass

from sqlalchemy import Column, ForeignKey, Table, Text

from sincpro_framework.data_layer.orm import TableRelation, map_aggregates


@dataclass
class InvoiceFiscal:                 # not an Entity: id, dates and version are the invoice's
    id: str = ""
    cufd: str = ""
    control_code: str = ""


def install(registry, invoice_class, invoice_table):
    table = Table(
        "invoice_fiscal",
        registry.metadata,
        Column("id", Text, ForeignKey(invoice_table.c.id, ondelete="CASCADE"), primary_key=True),
        Column("cufd", Text),
        Column("control_code", Text),
    )
    map_aggregates(
        registry,
        {InvoiceFiscal: table},
        relations={invoice_class: {
            "fiscal": TableRelation.foreign_key(
                InvoiceFiscal, parent_field="id", related_field="id", many=False)}},
    )
```

```python
from sincpro_framework.ddd import Criteria
from sincpro_framework.ddd.criteria import Specification

page = repository.search(Invoice, Criteria(specification=Specification({"fiscal": Criteria()})))
page[0].fiscal.cufd
```

The type checker does not see `Invoice.fiscal`, because it is installed at run time. That is the
cost of a relation installed from outside; a subclass, a consolidated record or the extension's own
class (`repository.get(InvoiceFiscal, id)`) are what a type checker follows.

## 11. Decisions pending

| # | Question | Leaning |
|---|---|---|
| 1 | Does the core reserve `properties` on **every** entity or only on those it marks extensible? | Only the marked ones (`properties_column()` is the core's to add): a table nobody extends should not carry it |
| 2 | May a **customer** define fields at run time, or only a module's developer? | Both, with the definition stored as data |
| 3 | Do the lines of the ledger, immutable once posted, keep extension fields in the slot or in a side table? | The slot: it avoids a second write per line |
| 4 | Does a list bring the fields of the extensions by default? | Yes, flat: the row is read as the extension it has. A core that must not pay for it answers only itself (`strict`) |
| 5 | Flat or delegated? | Flat by default; `delegated=[Core]` per entity |
| 6 | Does the loader enforce the core range, or only warn? | Enforced: a half-working ERP is worse than one that does not start |

## 12. Where it lives

```
ddd/entity/extensions.py            the registry: which classes extend which, whose each field is,
                                    the key a delegated answer nests it under, the overlays;
                                    no SQLAlchemy
ddd/query.py                        the answer: the fields of an extension flat or nested, the schema
ddd/editing/edits.py                `Edit` creating and editing as the class the values need
ddd/repositories/capabilities.py    `Extends`: `extend`, `reclassify`
data_layer/orm/sqlalchemy/
  entrypoint/mapping.py             `map_aggregates`: a facade over the services below
  entrypoint/template_table/        `properties_column`, `slot_field`
  services/extension_mapping.py     how the mapper is told a class extends another; the checks
                                    (`uses`, `requires`, a clashing column); `Consolidated`
  services/extension_typing.py      `ExtensionType`, the SQL expression that says what a row is
  services/extension_writing.py     the rows of an extension: give, remove, complete, reclassify
  services/sql_translator.py        a field of an extension as a column of a statement
  adapters/migrations/engine.py     `declared_elsewhere`: each chain leaves the columns of another
runtime/testing/contracts/extension.py   `ExtensionContract`
```

Dependencies run one way: `ddd` knows no table; the services know the registry; `map_aggregates`
and `Repository` know the services. The registries that are module-level (`extensions.EXTENSIONS`,
`extension_typing.PROOFS`) are filled by `map_aggregates`, never when a module is imported.
