# What shape is your system, and what to wire

Three shapes cover almost everything a service turns out to be. They use the same vocabulary —
an aggregate, a `Repository`, a `Criteria`, a `DomainEvent` — and differ only in what is wired
around it. **Every example on this page was run before it was written.**

| Your system | What you wire | Page |
|---|---|---|
| one database, foreign keys, everything in-process | `Database` + `Repository` + `ChangeTrackingMixin` | [§1](#1-one-database) |
| a context per database, facts crossing between them | …plus an outbox table and a relay | [§2](#2-a-database-per-context) |
| the facts *are* the state | …the events as the aggregates, plus a projection | [§3](#3-the-facts-are-the-state) |

You can start at §1 and grow into §2 without rewriting the domain. That is the point of the
shapes being the same vocabulary.

---

## 1. One database

A monolith with real foreign keys. The aggregate declares what it is; the table declares the
key; **the relation is inferred from the column** — you do not declare it twice.

```python
@dataclass
class Invoice(ChangeTrackingMixin, Entity):
    number: str = ""
    total: int = 0
    state: str = "draft"
    lines: list["Line"] = field(default_factory=list)

@dataclass
class Line(Entity):
    invoice_id: str = ""
    amount: int = 0

invoice_t = entity_table("invoice", md, Column("number", Text), Column("total", Integer),
                         Column("state", Text))
line_t = entity_table("line", md,
    Column("invoice_id", String, ForeignKey("invoice.id"), nullable=False),
    Column("amount", Integer))

map_aggregates(registry(), {Invoice: invoice_t, Line: line_t})   # the FK is enough
```

### What you get without asking

```python
with repository.context() as unit:
    invoice = unit.get(Invoice, invoice_id)
    invoice.total = 900
    invoice.state = "posted"          # no save(): the session already knows

for event in invoice.pull_events():
    # EntityUpdated(changes={"total": (100, 900), "state": ("draft", "posted")})
    publisher.publish(event)
```

**One event for the whole change, not one per field**, taken from what the engine is about to
write — so it sees every route to the database, including this one, where nobody called
`save()`. `ChangeTrackingMixin` on the aggregate is the whole opt-in. See
[change-tracking.md](events/change-tracking.md).

**A relation is read inside `context()`, or asked for by name.** Outside one it refuses rather
than quietly fetching the world:

```
RelationNotResolved: Invoice.lines was not asked for; name it in the criteria's
specification, or read it inside context()
```

### Wiring, in one direction

```python
BUSES: dict[str, UseFramework] = {}
publisher = Publisher(SyncQueue(lambda: Subscriber(*BUSES.values())))

reporting = UseFramework("reporting")
reporting.add_dependency("repository", repository)
reporting.add_dependency("publisher", publisher)
BUSES["reporting"] = reporting
```

**The queue is built before a single bus exists.** Handing it a function instead of a subscriber
is what cuts the knot: a queue needs a subscriber, a subscriber needs the buses, and a bus needs
the publisher the queue is behind. Nothing is asked for until somebody publishes, and by then
every bus is there. See [lifecycle.md](persistence/lifecycle.md).

---

## 2. A database per context

Each bounded context owns its database, so **there are no foreign keys between them** — and the
fact that crosses has to survive a crash between the write and the delivery.

### The fact and the write commit together

A domain event is an entity: the context maps its base event class to one table, and the
repository keeps what an aggregate recorded in the same transaction as the change:

```python
map_events(mapper, ExecutionDomainEvent, event_table("execution_events", md))

with execution_repo.context() as unit:
    run.advance("fitted")                # records RunAdvanced
    unit.save(run)                       # the run and its event, the same transaction
```

Either both landed or neither did. **This is the reason the event is kept in the transaction**:
publishing after the block commits is a window where a crash loses it for good.

### A relay sends it on

```python
@dataclass(kw_only=True)
class RunAdvanced(DeliverableEventMixin, ExecutionDomainEvent):   # it crosses: deliverable
    name = "execution.run.v1.advanced"


relay = EventRelay(execution_repo, ExecutionDomainEvent, publisher)
relay.run_once()     # from a cron: take what is due with SKIP LOCKED, hand on, mark each row
```

Every replica can run it: each takes different rows, and an event is marked delivered in the
same transaction it was taken in. A failure is what its policy says: tried again in place with a
backoff and then parked, by default. See
[persistence guide §12](persistence/guide.md#12-delivering-events).

### The contract that crosses

An event has two identities and **only the wire name travels**: a process boundary serialises to
JSON, and the publisher's Python class may not exist on the other side.

Put the event contract where every context can see it and give each one its own handler — one
class, N buses:

```python
@catalog.feature(RunAdvanced)      # the same class
@project.feature(RunAdvanced)
```

If a context genuinely cannot import it, it declares its own class under the same `name` and the
subscriber rebuilds the event as the class *that* bus registered. Both work; sharing the class
is simpler and cannot drift.

**N subscribers means N buses, not N handlers on one bus.** One bus answers one DTO in one
place, and registering a second is refused where you wrote it.

---

## 3. The facts are the state

Nothing is updated. Writing is appending an event, and the state is what the events add up to.
The entity is `EventSourcedMixin`: it has no row of its own, `repository.get` rebuilds it from its
events in the context's event table, and `repository.save` appends the new ones.

```python
@dataclass(kw_only=True)
class MoneyDeposited(BankDomainEvent):
    name = "bank.account.v1.money_deposited"
    amount: int = 0


@dataclass
class Account(EventSourcedMixin, Entity):
    event_base = BankDomainEvent
    balance: int = 0

    def deposit(self, amount: int) -> None:
        self.happened(MoneyDeposited(amount=amount))     # numbered, recorded, applied

    def apply(self, event: DomainEvent) -> None:
        if isinstance(event, MoneyDeposited):
            self.balance += event.amount
```

```python
account = repository.get(Account, "acc-1")      # rebuilt: its events, in order
account.deposit(300)
repository.save(account)                        # appended, entity_version = previous + 1
```

### Two writers, and reading fast

Each event carries its place in the entity's history, `entity_version`, and
`(entity_type, entity_id, entity_version)` is unique: of two writers rebuilt at the same version,
the second to save gets `StaleAggregate` and rebuilds.

Rebuilding on every read grows with the history. Snapshots are the next step; until then a read
model is an ordinary aggregate saved beside the events, and the events stay queryable with the
same `Criteria` as everything else:

```python
repository.measures(MoneyDeposited, Criteria(where=Condition(field="entity_id", value="acc-1")),
                    total=("count", "id"))
```

### What this shape costs

- **The events are forever.** A field you add today is missing from every event written before
  it, so an event's shape is versioned in its `name` (`bank.account.v1.…`) and a v2 is a new
  class.
- **There is no "just fix the row".** A correction is another event.
- **Replay to other systems is the broker's**: Kafka keeps the log; the table is this context's.

---

## Which one am I?

- **Everything in one database and one process** → §1. Do not build an outbox you do not need.
- **A context that must keep working when another is down**, or two teams that deploy apart
  → §2. The moment a fact crosses a process, it needs the outbox.
- **The history is the product** — audit, ledger, anything where "why is it this value" is a
  question somebody asks → §3.

Most systems are §1 growing into §2 for one or two facts. That is a normal shape, not a
half-migration: the aggregates do not change, a table and a relay appear.

---

## Four things that look like they need a store of their own

Each of these was written somewhere else first — a hand-rolled `sqlite3` store, a custom column
type, a refusal enforced by hand — because it did not look like the layer could do it. Each one
can. `tests/orm/test_recipes.py` runs all four.

### An aggregate identified by two columns

A composite key needs no special store. It is not an `Entity` — that convention mints one id,
and here the identity *is* the two facts:

```python
@dataclass
class RowLineage:
    table_fingerprint: str = ""
    lineage_id: str = ""
    position: int = 0

lineage_t = Table("row_lineage", md,
    Column("table_fingerprint", Text, primary_key=True),
    Column("lineage_id", Text, primary_key=True),
    Column("position", Integer))
```

`save`, `Criteria` and `count` work unchanged. `get` takes the identity, and the identity is the
pair, in the order the table declares it:

```python
repository.get(RowLineage, ("fp1", "a"))
```

### A fact that is written once

Append-only is a hook, not a mode the store has:

```python
@claims_hooks.on(Claim)
class WrittenOnce(Hook):
    def before_save(self, record: Claim) -> None:
        if not record.is_new:
            raise ContractViolation(f"{type(record).__name__} is written once and never replaced")

Repository(database, claims_hooks)
```

A second recording of the same fact is either identical or a contradiction, and replacing it
would silently accept the second.

### A list column on a row that predates the field

`JsonText` plus the field's own default. A row written before the field existed holds NULL, and
what comes back is what the aggregate declared — `[]`, not `None`. A field that declared
`| None` still gets the `None`: there the NULL is the answer.

### An event table, and the outbox pattern

A context's events are entities in one table (`event_table` + `map_events`), kept by the
repository with the change; the outbox is that table, with `DeliverableEventMixin` marking what
leaves and an `EventRelay` delivering it — no second table and no second API. See
[§2](#2-a-database-per-context) and [§3](#3-the-facts-are-the-state).

---

## Where to read next

| | |
|---|---|
| [persistence/introduction.md](persistence/introduction.md) | the aggregate, the repository, a first end-to-end example |
| [persistence/criteria.md](persistence/criteria.md) | the query language and what `dropped` means |
| [persistence/lifecycle.md](persistence/lifecycle.md) | what runs when something is written, and who each layer reaches |
| [persistence/hooks.md](persistence/hooks.md) | what a project puts around its own aggregates |
| [events/README.md](events/README.md) | publisher, queues, subscriber |
| [events/change-tracking.md](events/change-tracking.md) | one `Updated` event with everything that changed |
