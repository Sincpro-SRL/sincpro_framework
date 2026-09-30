# The aggregate

## `Entity` gives four things

`id` (UUID v7), `created_at`, `updated_at`, `version`. `version` is how `save` knows insert (0, never
stored) from update. `updated_at` is when it was last written — `created_at` on insert — so
"untouched since" is one column.

```python
from dataclasses import dataclass, field
from sincpro_framework.ddd import Entity, EntityCollection


@dataclass
class Customer(Entity):
    name: str = ""
    email: str = ""
    invoices: list["Invoice"] = field(default_factory=list)   # one2many — read off the FK


@dataclass
class Invoice(Entity):
    number: str = ""
    customer_id: str = ""
    total: int = 0
    state: str = "draft"
    customer: Customer | None = None                           # many2one — same FK


class Customers(EntityCollection[Customer]): ...
class Invoices(EntityCollection[Invoice]): ...
```

**Relations are annotations.** `list["Invoice"]` is one2many, `Customer | None` is many2one; the
column linking them is read from the table's foreign key. Do not declare the relation twice.

**`EntityCollection[...]`** is the typed page a search answers: `items`, `count`, `cursor`,
`dropped`, `meta`. Declaring one per aggregate is optional — `search(Invoice, ...)` works — but it
types the answer and gives the collection its own methods.

## Mixins add a capability and its columns

| Mixin | Adds | Then |
|---|---|---|
| `ArchivableMixin` | `archived_at` | `repository.archive(record)` hides it; reads skip it unless asked |
| `AuditedMixin` | `created_by`, `updated_by` | stamped by the session's `before_flush`, for every write path |
| `ChangeTrackingMixin` | one `EntityUpdated` event per save | see `sincpro-framework-domain-events` |

```python
@dataclass
class Customer(ArchivableMixin, Entity):
    name: str = ""
```

The table must include the mixin's columns: `archive_columns()`, `audit_columns()`.

## Models are dataclasses; anything that travels is a DTO

- `@dataclass` for aggregates, entities and value objects (mapped imperatively by the ORM, free of
  framework imports).
- `DataTransferObject` (pydantic) for anything crossing a boundary: Commands, Responses, `Criteria`,
  events. Validation and serialization at both ends.

## Value object vs entity

Two with identical fields being *the same thing* → value object; not the same → entity. A value
object appears when there is a **rule to defend** (`Money` that must not cross currencies, a
`Cursor`), not for tidiness. An id with no rule stays a `str`.

```python
from sincpro_framework.ddd import ValueObject

Email = ValueObject(str, validate=lambda v: "@" in v)
```

## Extending an aggregate

A subclass keeps inherited fields in the parent's table and its own in its table, which references
the parent's key. `map_aggregates` maps the parent first whatever the order and refuses a table that
does not reference the parent's key.

```python
@dataclass
class Note(Entity):
    number: str = ""
    total: int = 0

@dataclass
class CreditNote(Note):
    reason: str = ""

credit_reason_table = Table(
    "credit_note_reason", credit.metadata,
    Column("id", Text, ForeignKey("credit_note.id"), primary_key=True),
    Column("reason", Text, nullable=False),
)
map_aggregates(credit, {Note: credit_note_table, CreditNote: credit_reason_table})
```

A `Criteria` filters and orders by inherited and own fields alike.

## Full plan of computing

- Over the record's own fields → a method on the aggregate (pure, no I/O, testable without a DB).
- Over a set → a method on the collection.
- Needs other aggregates or too expensive → a stored field fed by a domain event.

**A computed property never performs I/O.** If it needs a query it is a relation, a stored field,
or a use case — not a property.
