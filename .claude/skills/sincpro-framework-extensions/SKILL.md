---
name: sincpro-framework-extensions
description: Add fields to an entity you do not own with sincpro_framework — a module in another repository extends a core entity (a delivery to a move, a fiscal number to an invoice) without editing it, and the core's own Get/Search/Edit still work. Covers a subclass with a table of its own or in the entity's table, a flat record of sibling extensions (Consolidated), delegated or flat answers, the contract a module declares (CONTRACT, uses, requires), the overlay of presentation, the JSON slot, migrations of a table two chains declare, and the ExtensionContract a module runs in its CI. Use whenever a task extends, customises or installs a module on an existing entity.
---

# sincpro-framework-extensions

A module adds to an entity of the core **without editing it**. The rule: *what an extension adds,
the extension owns* — its table, its migrations, its release. The core never names the extension;
the extension names the core only by its opaque `id`. This skill stands alone; the deep docs it
names live in the framework repository, not in the package.

## Context

- **Use it** when another repository adds data to an entity (a customer's fiscal fields, a
  distribution module's delivery) and the core's commands must go on working.
- **Do not use it** for a concept with behaviour of its own (distribution: routes, vehicles): that is
  a bounded context with its own bus and database, reached by `Relation.bus`. And not to reuse
  behaviour across the core's own entities: that is a mixin, inside the core.
- A **mixin is not how another repository extends an entity**: the final class would be assembled by
  whoever deploys it, in the order of its bases, and the type checker cannot follow it.

## Abstractions

| Term | What it is | Kind | Import |
|---|---|---|---|
| Extension | A subclass of the entity with a table whose `id` is the parent's, or with columns in the parent's own table | class + table | `map_aggregates(reg, {MoveDelivery: delivery_table})` |
| `Consolidated` | One flat class over the outer join of an entity and its extensions: siblings in one record | declaration | `from sincpro_framework.data_layer.orm import Consolidated` |
| `CONTRACT` | `ClassVar[str] = "2.3"` on a core entity: a major that breaks, a minor that adds | convention | on the entity |
| `uses` / `requires` | What a module reads from the core / the contract it was written against; checked when it is mapped | parameters | `map_aggregates(…, uses={Move: ("total",)}, requires={Move: "2.1"})` |
| `strict` / `delegated` | A core that answers only itself / whose answers nest each extension under its key | parameters | `map_aggregates(…, strict=[Move])`, `delegated=[Move]` |
| `overlay` | How a module shows a field of the core: `label`, `help`, `readonly`, `required` | parameter | `map_aggregates(…, overlay={Move: {"total": {"readonly": True}}})` |
| `TableRelation.foreign_key(…, many=False)` | A relation an extension installs on a class it does not own, saying its own cardinality | function | `from sincpro_framework.data_layer.orm import Relation` |
| `properties_column` / `slot_field` | The JSON slot a core reserves, and a key of it as a field | functions | `from sincpro_framework.data_layer.orm import template_table` |
| `Repository.extend` / `reclassify` | Give a stored record the rows of an extension / write the stored type of every row | methods | on the `Repository` (capability `Extends`) |
| `ExtensionContract` | What a module runs in its CI against the core it will be installed on | test base class | `from sincpro_framework.runtime.testing.contracts.extension import ExtensionContract` |
| `FieldMeta.owner` | The extension that adds a field (`"MoveDelivery"`); `None` for the entity's own | field | `ModelIntrospection.describe(Move).fields["route"].owner` |

## Architecture

```
ddd/entity/extensions.py            registry: which classes extend which, whose a field is   (no SQLAlchemy)
data_layer/orm/sqlalchemy/
  entrypoint/mapping.py             map_aggregates — a facade
  services/extension_mapping.py     how the mapper is told; uses / requires / clashing column; Consolidated
  services/extension_typing.py      ExtensionType: the SQL expression that says what a row is
  services/extension_writing.py     the rows of an extension: give, remove, complete, reclassify
```

**Reading.** `search(Move)` returns each row as the most derived extension it has (`Move`,
`MoveDelivery`, …): the type is worked out from the extension rows that exist — one more statement
per type found, not a join per extension. A core whose table has a nullable `extension_type`
column stores it instead, and the cost stops depending on how many extensions are installed.

**Writing.** Saving the deepest class writes every table, the core's first, in one transaction.
`Edit` written for the core creates and edits as the class the values need.

## Mistakes an agent makes

- **Adding an attribute to the core's class, or merging modules into one class** — `setattr` on a
  class you do not own is invisible to the type checker and to the next release. Subclass it.
- **Two sibling extensions as one subclass** — a class cannot inherit two mapped entities. A module
  that knows both declares `Consolidated(Core, A, B)`; otherwise they are two edits.
- **Giving an extension a column the core also has** — refused when it is mapped, naming both tables:
  a core release adding a field an extension already had would otherwise overwrite the core's data.
- **Ordering the core by a field of an extension** — refused (a record may lack it). Filtering and
  grouping work through a subquery; a report over millions of rows reads a `Consolidated` (a join).
- **A consolidated class that is saved but leaves out a `NOT NULL` column** — the insert fails.
  One that is only read may omit columns.
- **Forgetting the migration of a shared table** — an extension that stores columns in the core's
  table has its own chain; `Migrations` tells each Alembic chain who else declares the table, and
  each leaves the other's columns alone. A hand-written step that drops them is the one that does
  not.
- **Editing a stored `Move` with `route` through a store that is not a `Repository`** — only a store
  with the `Extends` capability can give a record the rows of an extension.
- **Expecting generated stubs, `move.ext(Type)` or a build step** — there are none on purpose: the
  extension is a class, and the type checker follows classes.

## How

```python
@dataclass
class MoveDelivery(Move):            # a module in another repository
    route: str = ""

delivery_table = Table("move_delivery", tables,
                       Column("id", Text, ForeignKey("move.id"), primary_key=True),
                       Column("route", Text))
map_aggregates(registry, {MoveDelivery: delivery_table},
               requires={Move: "2.1"}, uses={Move: ("number", "total")})

store.search(Move, Criteria(where=Condition(field="route", value="R7")))   # the core's own query
```

A `Get`/`Search`/`Edit` written for `Move` before the module existed now answers `route`, flat, and
the answer's schema describes it. The module's CI:

```python
class TestDeliveryExtension(ExtensionContract):
    entity, extension = Move, MoveDelivery
    uses, requires = ("number", "total"), "2.1"
    def make_store(self): ...
```

## References

- `docs/persistence/extensions.md` — the guide, every block run by the tests
- `docs/spec/extensibility.md` — the requirements, the rule and its reasons, what a release does,
  the measurements, what is not built and why

## Related

- The aggregate, table, repository and unit of work these extend: `sincpro-framework-persistence`
- The `Criteria` and `Specification` that name an extension's fields: `sincpro-framework-criteria`
- Migrations, including a table two chains declare: `sincpro-framework-operations`
- Another bounded context instead of an extension (`Relation.bus`): `sincpro-framework-persistence`
