# The aggregate

## `Entity` gives four things

`id` (UUID v7), `created_at`, `updated_at`, `version`. `version` is how `save` knows insert (0, never
stored) from update. `updated_at` is when it was last written — `created_at` on insert — so
"untouched since" is one column.

One aggregate per file, named after it:

```python
# domain/customer.py
from dataclasses import dataclass, field

from sincpro_framework.ddd import Entity, EntityCollection


@dataclass
class Customer(Entity):
    name: str = ""
    email: str = ""
    invoices: list["Invoice"] = field(default_factory=list, repr=False, compare=False)  # one2many


class Customers(EntityCollection[Customer]): ...


from .invoice import Invoice  # noqa: E402 — at the bottom, never under TYPE_CHECKING
```

```python
# domain/invoice.py
from dataclasses import dataclass, field

from sincpro_framework.ddd import Entity, EntityCollection

from .customer import Customer


@dataclass
class Invoice(Entity):
    number: str = ""
    customer_id: str = ""
    total: int = 0
    state: str = "draft"
    customer: Customer | None = field(default=None, repr=False, compare=False)  # many2one


class Invoices(EntityCollection[Invoice]): ...
```

**Two aggregates that reference each other** import the second at the **bottom** of the first
module. `map_aggregates` resolves annotations at runtime against the module's globals
(`get_type_hints`), so an import under `if TYPE_CHECKING:` fails at boot with
`ContractViolation: Customer names a type that cannot be resolved at runtime`.

**Relation fields are `repr=False, compare=False`.** The dataclass `repr`, `==` and f-strings read
every field; a relation not read raises `RelationNotResolved` outside `context()`, so a record that
declares it could not be logged or compared in a test. `asdict()` still reads every field: never
`asdict` an aggregate, build the Response field by field.

**Relations are annotations.** `list["Invoice"]` is one2many, `Customer | None` is many2one; the
column linking them is read from the table's foreign key. Do not declare the relation twice.

**`EntityCollection[...]`** is the typed page a search answers: `items`, `count`, `cursor`,
`dropped`, `meta`. Declaring one per aggregate is optional — `search(Invoice, ...)` works — but it
types the answer and gives the collection its own methods.

## Mixins add a capability and its columns

| Mixin | Adds | Then |
|---|---|---|
| `ArchivableMixin` | `archived_at` | `repository.archive(record)` hides it; reads skip it unless asked |
| `AuditedMixin` | `created_by`, `updated_by` | `created_by` on insert, `updated_by` on each later update (`None` until then), from `Database(url, actor=lambda: …)`; without an actor they stay `None` |
| `ChangeTrackingMixin` | one `EntityUpdated` per save of a stored aggregate; an insert records nothing | see `sincpro-framework-domain-events` |

```python
@dataclass
class Customer(ArchivableMixin, Entity):
    name: str = ""
```

Mixins go before `Entity` in the bases. The table must include the mixin's columns —
`*archive_columns()`, `*audit_columns()` — or the mixin's fields are never stored and the
capability silently does nothing.

## Models are dataclasses; anything that travels is a DTO

- `@dataclass` for aggregates, entities and domain events (`DomainEvent` is itself an `Entity`),
  mapped imperatively by the ORM and free of framework imports beyond `sincpro_framework.ddd`.
- `DataTransferObject` (pydantic) for anything crossing a boundary: Commands, Responses,
  `Criteria`. Validation and serialization at both ends.

## Value object vs entity

Two with identical fields being *the same thing* → value object; not the same → entity. A value
object appears when there is a **rule to defend** (`Money` that must not cross currencies, a
`Cursor`), not for tidiness. An id with no rule stays a `str`.

```python
@dataclass(frozen=True)
class Email:
    value: str

    def __post_init__(self) -> None:
        if "@" not in self.value:
            raise ValueError(f"{self.value!r} is not an email")


Email("ana@acme.bo")          # Email(value='ana@acme.bo')
```

The check lives on the type, never in a loose module-level `def`. The `ValueObject(base,
validate_fn, name)` factory makes a validated primitive instead (`Email("ana@acme.bo")` stays a
`str`): its `validate_fn` is a `@staticmethod` of a type, and it **raises** to refuse — whatever
non-`None` value it returns **replaces** the input, so a predicate such as `lambda v: "@" in v`
would silently turn the text into `'True'`.

## Extending an aggregate

A subclass keeps inherited fields in the parent's table and its own in its table, which references
the parent's key (joined-table inheritance). `map_aggregates` maps the parent first whatever the
order and refuses a table that does not reference the parent's key.

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

## Where a computation goes

- Over the record's own fields → a method on the aggregate (pure, no I/O, testable without a DB).
- Over a set → a method on the collection.
- Needs other aggregates or too expensive → a stored field fed by a domain event.

**A computed property never performs I/O.** If it needs a query it is a relation, a stored field,
or a use case — not a property.
