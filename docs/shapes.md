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

An event is an `Entity`, so the repository already stores one. `EventTrackableMixin` adds its
delivery state, and that is an outbox — no new class:

```python
@dataclass(kw_only=True)
class Outbox(EventTrackableMixin, RunAdvanced):
    name = "execution.v1.run_advanced"

outbox_t = entity_table("outbox", md, *event_columns(), *delivery_columns(),
                        Column("run_id", Text), Column("stage", Text),
                        Index("outbox_pending", "status"))
```

```python
with execution_repo.context() as unit:
    run.stage = "fitted"
    unit.save(run)
    unit.save(Outbox(run_id=run.id, stage="fitted"))   # the same transaction
```

Either both landed or neither did. **This is the reason the outbox exists**: publishing after
the block commits is a window where a crash loses the fact for good.

### The relay: claim, deliver, acknowledge

```python
PENDING = Criteria(where=Condition(field="status", value=EventStatus.PENDING.value))

with execution_repo.context() as unit:
    claimed = unit.search(Outboxes, PENDING, for_update=True, skip_locked=True)
    for one in claimed:
        one.mark_processing()
        unit.save(one)

for one in claimed:
    publisher.publish(RunAdvanced(run_id=one.run_id, stage=one.stage))

with execution_repo.context() as unit:
    for one in claimed:
        one.mark_acknowledged()
        unit.save(one)
```

`for_update=True, skip_locked=True` is what lets two relays run without ever taking the same
row. On SQLite those are no-ops, so a test there proves the shape and not the exclusion — run
that one against Postgres.

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

Nothing is updated. Writing is appending, and the state is what the facts add up to.

```python
@dataclass(kw_only=True)
class MoneyDeposited(DomainEvent):
    name = "bank.v1.money_deposited"
    account_id: str = ""
    amount: int = 0

deposited_t = entity_table("deposited", md, *event_columns(),
                           Column("account_id", Text), Column("amount", Integer))
map_aggregates(registry(), {MoneyDeposited: deposited_t, ...})
```

```python
repository.save(AccountOpened(account_id=account, holder="Ana"))
repository.save([MoneyDeposited(account_id=account, amount=500),
                 MoneyDeposited(account_id=account, amount=300)])
repository.save(MoneyWithdrawn(account_id=account, amount=200))
```

### Rebuilding, and reading fast

```python
mine = Criteria(where=Condition(field="account_id", value=account), order=(Sort(field="id"),))
balance = (sum(one.amount for one in repository.fetch_all(Deposits, mine))
           - sum(one.amount for one in repository.fetch_all(Withdrawals, mine)))
# 500 + 300 - 200 = 600
```

Ordering by `id` **is** ordering by time: an id is a UUID v7.

Rebuilding on every read does not scale, so the answer is kept beside the facts as a projection
— an ordinary aggregate, saved like any other:

```python
repository.save(Balance(account_id=account, holder="Ana", amount=balance))
```

And the history stays queryable with the same `Criteria` as everything else:

```python
repository.measures(Deposits, mine, total=("sum", "amount"))   # {"total": 800}
```

### What this shape costs

- **The facts are forever.** A field you add today is missing from every row written before it,
  so an event's shape is versioned in its `name` (`bank.v1.…`) and a v2 is a new class.
- **There is no "just fix the row".** A correction is another fact.
- The framework has no "refuse to replace" mode: append-only is a discipline here, not something
  the store enforces.

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

Append-only is a rule, not a mode the store has:

```python
def written_once(record: Claim) -> None:
    if not record.is_new:
        raise ContractViolation(f"{type(record).__name__} is written once and never replaced")

Repository(database, rules=[Rule(entity=Claim, before_save=written_once)])
```

A second recording of the same fact is either identical or a contradiction, and replacing it
would silently accept the second.

### A list column on a row that predates the field

`JsonText` plus the field's own default. A row written before the field existed holds NULL, and
what comes back is what the aggregate declared — `[]`, not `None`. A field that declared
`| None` still gets the `None`: there the NULL is the answer.

### An event store, and an outbox

A `DomainEvent` is an `Entity`, so the repository that already exists stores and queries one.
`event_columns()` is the envelope, `delivery_columns()` is what turns the table into an outbox.
Neither is a second abstraction — see [§2](#2-a-database-per-context) and
[§3](#3-the-facts-are-the-state).

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
