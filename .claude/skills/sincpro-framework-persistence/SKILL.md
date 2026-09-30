---
name: sincpro-framework-persistence
description: Persist aggregates with sincpro_framework — declare an Entity/aggregate, map its table, get/save through the Repository, wrap writes in a unit of work, resolve relations, and put rules on hooks or mixins. Use whenever the task touches aggregates, tables, `Repository`, `save`/`remove`/`archive`, `repository.context()`, `entity_table`, `map_aggregates`, relations, `Hooks`, or `ArchivableMixin`/`AuditedMixin`/`ChangeTrackingMixin` in a Sincpro Python service.
---

# sincpro-framework-persistence

`sincpro_framework.ddd` is the vocabulary (aggregates, `Repository`, events) and needs nothing
installed. `sincpro_framework.orm` is the SQLAlchemy adapter:
`pip install sincpro-framework[sqlalchemy]`. A Feature reads and writes through `self.repository`
and never learns which database is behind it.

The full step-by-step, runnable as a test, is `docs/persistence/guide.md`. The references here are
the distilled contract; when they are not enough, that page is.

## The shape

```python
# domain/
@dataclass
class Invoice(Entity):                      # id (UUID v7), created_at, updated_at, version
    number: str = ""
    customer_id: str = ""
    total: int = 0
    state: str = "draft"
    customer: Customer | None = None        # many2one — inferred from the FK

class Invoices(EntityCollection[Invoice]): ...

# infrastructure/ (tables.py)
from sincpro_framework.orm import Database, Repository, entity_table, map_aggregates

billing = registry()
invoice_table = entity_table(
    "invoice", billing.metadata,
    Column("number", Text, nullable=False, unique=True),
    Column("customer_id", Text, ForeignKey("customer.id"), nullable=False),
    Column("total", Integer, nullable=False),           # a page orders only by NOT NULL columns
    Column("state", Text, nullable=False),
)
map_aggregates(billing, {Customer: customer_table, Invoice: invoice_table})
repository = Repository(Database("postgresql://…"))

# services/register_customer.py
@billing_bus.feature(CommandRegisterCustomer)
class RegisterCustomer(Feature):
    repository: Repository                  # declared on DependencyContextType
    def execute(self, dto: CommandRegisterCustomer) -> ResponseRegisterCustomer:
        customer = Customer(name=dto.name, email=dto.email)
        self.repository.save(customer)
        return ResponseRegisterCustomer(customer_id=customer.id)
```

## The rules that matter

- **An aggregate is a plain `@dataclass` inheriting `Entity`.** It imports nothing from the
  database. `id` is a UUID v7, so ordering by `id` is ordering by time.
- **One table per aggregate, declared once in infrastructure.** `entity_table` adds the four
  `Entity` columns; `map_aggregates` infers relations from foreign keys.
- **Writes are explicit, reads are generic.** `save` one or many, `remove`, `archive`. There is no
  generic `update`/`delete` — that is where the generic-repository criticism is right.
- **A stale write is refused** (`StaleAggregate`); `Repository.retrying(...)` retries.
- **`repository.context()` is one unit of work**: everything commits together or not at all.
- **A relation is resolved once per page, and refused if you did not ask for it.** No hidden N+1.
- **The aggregate records events; the Feature publishes them.** The aggregate never publishes.

## References

- [references/aggregate.md](references/aggregate.md) — `Entity`, mixins, collections, value objects
- [references/repository-and-writes.md](references/repository-and-writes.md) — tables, `save`/`remove`/`archive`, unit of work, reads
- [references/relations.md](references/relations.md) — the kinds and how each resolves
- [references/hooks-and-mixins.md](references/hooks-and-mixins.md) — `Rule`/`Hook`, moments, mixins

## Related

- Queries and listings: `sincpro-framework-criteria`
- Domain events, change tracking, event sourcing, outbox: `sincpro-framework-domain-events`
- Testing without a database: `framework_critical_testing_strategy`, `MemoryRepository`
