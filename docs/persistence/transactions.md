# Transactions

What a unit of work promises, how it is configured, what it says when it loses, and the writes
that go past an aggregate on purpose. Every block on this page runs, in order, as one program —
`tests/docs/test_persistence_guide.py` proves it.

| You need | Section |
|---|---|
| One transaction for several writes, configured | [1. The unit of work](#1-the-unit-of-work) |
| A root saved with the children it owns | [2. The aggregate, whole](#2-the-aggregate-whole) |
| Two requests that must not both pass | [3. Locks and isolation](#3-locks-and-isolation) |
| To know what the database refused, and to run it again | [4. What the engine refuses](#4-what-the-engine-refuses) |
| To publish only what committed | [5. After the commit](#5-after-the-commit) |
| An idempotent import, a mass update | [6. Writes past the aggregate](#6-writes-past-the-aggregate) |
| Several use cases validated and committed together | [7. Several use cases, one transaction](#7-several-use-cases-one-transaction) |
| A correlative without gaps — a fiscal invoice's number | [8. Numbers without gaps](#8-numbers-without-gaps) |
| SQLAlchemy itself | [9. The passthrough](#9-the-passthrough) |

The rules behind every section are in the [manifesto](manifesto.md).

## 1. The unit of work

A treasury: accounts whose balance may never go negative, and the movements each one owns.

```python
from dataclasses import dataclass, field

from sqlalchemy import CheckConstraint, Column, ForeignKey, Integer, Text
from sqlalchemy.orm import registry

from sincpro_framework.ddd import Entity
from sincpro_framework.data_layer.orm import Database, Isolation, Repository, map_aggregates, template_table


@dataclass
class Movement(Entity):
    account_id: str = ""
    amount: int = 0                                  # minor units, never a float
    memo: str = ""


@dataclass
class Account(Entity):
    holder: str = ""
    balance: int = 0
    movements: list[Movement] = field(default_factory=list)

    def withdraw(self, amount: int, memo: str) -> None:
        self.balance -= amount
        self.movements = [*self.movements, Movement(amount=-amount, memo=memo)]


treasury = registry()
account_table = template_table.entity_table(
    "account",
    treasury.metadata,
    Column("holder", Text, nullable=False, unique=True),
    Column("balance", Integer, nullable=False),
    CheckConstraint("balance >= 0", name="balance_never_negative"),
)
movement_table = template_table.entity_table(
    "movement",
    treasury.metadata,
    Column("account_id", Text, ForeignKey("account.id"), nullable=False),
    Column("amount", Integer, nullable=False),
    Column("memo", Text, nullable=False),
)
map_aggregates(treasury, {Account: account_table, Movement: movement_table})

database = Database("sqlite:///treasury.sqlite3", enforce_foreign_keys=True)
treasury.metadata.create_all(database.engine)
repository = Repository(database)
```

`context()` is one transaction: everything inside commits when the block ends, or nothing does
if it raises. It is configured where it begins — the only moment an isolation level can still be
chosen:

```python
with repository.context(isolation=Isolation.SERIALIZABLE) as unit:
    unit.save(Account(holder="ana", balance=100))
    unit.save(Account(holder="bob", balance=50))

with repository.context(read_only=True) as unit:      # a report: any write is refused
    assert unit.count(Account).value == 2
```

| Option | Means | Where the engine lacks it |
|---|---|---|
| `isolation` | `Isolation.READ_COMMITTED`, `REPEATABLE_READ`, `SERIALIZABLE` | refused — `ContractViolation`; SQLite runs every level as `serializable`, which honours it |
| `read_only` | no write may leave the block; Postgres is asked too | always honoured: the refusal is the framework's |
| `timeout` | seconds any one statement may run (`SET LOCAL statement_timeout`) | warned, statements run unbounded |
| `engine` | SQLAlchemy's `execution_options`, as they come | — |

SQLite has only `serializable`, which is stronger than any level asked of it, so a Feature that
asks `Isolation.REPEATABLE_READ` runs there as it will on Postgres or better — never weaker. A nested
`context()` joins the transaction in play; one that asks for different options is refused,
because that transaction has already begun. A `read_only` block hands back what it read, usable
after it, and still runs its `after_commit`.
`savepoint()` is the part of a block that can fail alone; `commit()` makes a long block durable
batch by batch.

**What is written is what was saved.** An aggregate changed inside the block and never handed to
`save` is put back when the block ends — and at each `commit()` — and named in the log; a read
after the change does not write it either. The commit never writes past the aggregate's hooks
and cascade. `context(writes=Writes.CHANGED)` makes it write what the block changed on what it
loaded, as the session tracks it: Fowler's object registration, where the default is his caller
registration — the same choice Doctrine's `DEFERRED_EXPLICIT`, Django, Rails and Ecto make.

```python
from sincpro_framework.data_layer.orm import Writes

with repository.context() as unit:
    forgotten = unit.get_by(Account, holder="bob")
    forgotten.balance = 0                              # never saved: put back, and logged
assert repository.get_by(Account, holder="bob").balance == 50

with repository.context(writes=Writes.CHANGED) as unit:
    unit.get_by(Account, holder="bob").holder = "bob"  # written by the commit, saved or not
```

## 2. The aggregate, whole

`save` writes a root with the children it owns, and assigning the relation settles the ones it no
longer holds ([relations §8](relations.md#8-an-aggregate-is-saved-whole)).

```python
with repository.context() as unit:
    ana = unit.get_by(Account, holder="ana")
    ana.withdraw(30, "rent")
    unit.save(ana)                                     # the account and its new movement

[rent] = repository.fetch_all(Movement).items
assert (rent.amount, rent.account_id) == (-30, ana.id)
```

## 3. Locks and isolation

Two withdrawals must not both pass a balance check. The rule lives on the aggregate; that two
requests do not both pass it is the unit of work's job, with one of two tools:

```python
with repository.context() as unit:
    held = unit.get_by(Account, holder="bob")
    locked = unit.get(Account, held.id, for_update=True)    # the row is ours until the commit
    locked.withdraw(20, "groceries")
    unit.save(locked)
```

`for_update` takes the row lock; `skip_locked` passes over rows another worker holds — the relay
of an outbox — and `nowait` fails at once instead of waiting. The two cannot both be asked.
SQLite has no row locks and ignores all three; on Postgres a held row under `nowait` is a
`TimedOut`. The other tool is `isolation=Isolation.SERIALIZABLE` with `retrying` (§4): no lock is taken,
and the transaction that loses is run again.

## 4. What the engine refuses

The driver's error is named by what happened, in the `ddd` vocabulary:

| The engine said | Raised | Run it again? |
|---|---|---|
| a unique value already taken | `DuplicateAggregate` | no — resolve the twin |
| a reference to a row that does not exist; an empty or invalid value | `ConstraintViolation` | no — the write itself is wrong |
| a newer version of what was read | `StaleAggregate` | yes, on a fresh read |
| a serialization failure, a deadlock | `TransactionConflict` | yes, on a fresh read |
| a lock it was told not to wait for (`nowait`), a statement past `timeout` | `TimedOut` | no — the bound was the caller's |

```python
from sincpro_framework.ddd import ConstraintViolation, DuplicateAggregate

try:
    repository.save(Account(holder="ana", balance=1))
    raise AssertionError("a second ana should have been refused")
except DuplicateAggregate:
    pass

overdrawn = repository.get_by(Account, holder="bob")
overdrawn.balance = -1
try:
    repository.save(overdrawn)
    raise AssertionError("the table refuses a negative balance")
except ConstraintViolation as refused:
    assert "a rule the table holds" in str(refused)
```

`retrying` runs a unit of work again for the two races a fresh read resolves —
`StaleAggregate` and `TransactionConflict` — and never for the others. It wraps a whole unit of
work, so it is called around `context()`, never inside one:

```python
def pay_rent() -> None:
    with repository.context(isolation=Isolation.SERIALIZABLE) as unit:
        account = unit.get_by(Account, holder="ana")
        account.withdraw(10, "rent")
        unit.save(account)


repository.retrying(pay_rent)
assert repository.get_by(Account, holder="ana").balance == 60
```

## 5. After the commit

A fact is told to the world only once the transaction that made it committed. `after_commit`
belongs to the unit of work that registered it:

```python
told: list[str] = []

with repository.context() as unit:
    bob = unit.get_by(Account, holder="bob")
    bob.withdraw(5, "coffee")
    unit.save(bob)
    unit.after_commit(lambda: told.append(f"bob paid, balance {bob.balance}"))
    assert told == []                                  # not yet: this can still be undone

assert told == ["bob paid, balance 25"]

try:
    with repository.context() as unit:
        unit.after_commit(lambda: told.append("never told"))
        unit.after_rollback(lambda: told.append("undone, let go"))
        raise RuntimeError("the gateway is down")
except RuntimeError:
    pass
assert told[-1] == "undone, let go"
```

Registered inside a `savepoint()` that rolls back, an `after_commit` is dropped with it. Each
`commit()` of a long block runs what was registered before it. A callback that raises is logged
and the rest still run — the commit stands, so the caller is not told it did not. A callback
writes through a new unit of work: this one has ended.

## 6. Writes past the aggregate

Two verbs skip the aggregate's machinery on purpose and say so in their name.

**`upsert`** inserts, or overwrites the stored record holding the same key — the import that must
be idempotent, the sync that mirrors another system. No version check: it overwrites by
definition. The key must be one the table holds unique.

```python
@dataclass
class Rate(Entity):
    currency: str = ""
    per_usd: int = 0


rates = registry()
rate_table = template_table.entity_table(
    "rate",
    rates.metadata,
    Column("currency", Text, nullable=False, unique=True),
    Column("per_usd", Integer, nullable=False),
)
map_aggregates(rates, {Rate: rate_table})
rates.metadata.create_all(database.engine)

first = repository.upsert(
    [Rate(currency="BOB", per_usd=696), Rate(currency="EUR", per_usd=92)], on=("currency",)
)
again = repository.upsert(Rate(currency="BOB", per_usd=697), on=("currency",))   # overwritten
kept = repository.upsert(Rate(currency="EUR", per_usd=1), on=("currency",), update=())
assert (first.written, again.written, kept.skipped) == (2, 1, 1)
assert repository.get_by(Rate, currency="BOB").per_usd == 697
```

`update=("per_usd",)` names what a conflict overwrites; `update=()` leaves the stored row alone.
It answers `Upserted(written, skipped)` per distinct key: inserted or overwritten, and left as it
was — a conflict with nothing to overwrite, or a stored row outside the repository's scope, which
it never overwrites. `before_save`/`after_save` run, `before_create`/`before_update` do not —
which one happened only the database knows. The records handed in are not refreshed: read them
again to change them. Postgres and SQLite (3.35+) are spoken; MySQL's `ON DUPLICATE KEY` is not —
it matches any unique key rather than the one named and cannot count what it wrote.

**`update_all` and `remove_all`** write every row a filter matches, in one statement, and answer
how many:

```python
from sincpro_framework.ddd import Condition, Criteria, Operator

small = Criteria(where=Condition(field="amount", operator=Operator.GT, value=-10))
assert repository.update_all(Movement, small, {"memo": "petty cash"}) == 1
assert repository.remove_all(Movement, small) == 1
```

No hook, no cascade, no change tracking — what Django's `update()` and Rails' `update_all` skip
too. What they keep is what makes them safe: `version` is raised so a stale copy is still
refused later, `updated_at` is stamped, the scope a repository was narrowed to and the archived
rows apply as in a read. **They never run wider than they were asked**: a page cannot bound a
write and is refused, and so is a condition the aggregate cannot answer — in a read it would be
dropped, in a write it would widen it. A row something still points at refuses `remove_all` as a
`ConstraintViolation`.

## 7. Several use cases, one transaction

A unit of work opened in an `ApplicationService` is the one every Feature it calls writes in: a
repository on the same `Database` joins the unit of work in play instead of committing on its own.
Several flows are validated together and committed together — or none of them is.

```python
from sincpro_framework import ApplicationService, DataTransferObject, Feature, UseFramework

repository.save([Account(holder="carla", balance=100), Account(holder="dan", balance=0)])
treasury_bus = UseFramework("treasury", log_after_execution=False)
treasury_bus.add_dependency("repository", repository)


class CommandMove(DataTransferObject):
    holder: str
    amount: int                                      # negative takes, positive gives


class ResponseMove(DataTransferObject):
    balance: int


class CommandTransfer(DataTransferObject):
    source: str
    target: str
    amount: int


class ResponseTransfer(DataTransferObject):
    source_balance: int


@treasury_bus.feature(CommandMove)
class Move(Feature):
    repository: Repository

    def execute(self, dto: CommandMove) -> ResponseMove:
        account = self.repository.get_by(Account, holder=dto.holder)
        account.withdraw(-dto.amount, "transfer")
        self.repository.save(account)                # joins the unit of work in play
        return ResponseMove(balance=account.balance)


@treasury_bus.app_service(CommandTransfer)
class Transfer(ApplicationService):
    repository: Repository

    def execute(self, dto: CommandTransfer) -> ResponseTransfer:
        with self.repository.context(isolation=Isolation.SERIALIZABLE):
            self.feature_bus(CommandMove(holder=dto.target, amount=dto.amount), ResponseMove)
            taken = self.feature_bus(
                CommandMove(holder=dto.source, amount=-dto.amount), ResponseMove
            )
        return ResponseTransfer(source_balance=taken.balance)


done = treasury_bus(CommandTransfer(source="carla", target="dan", amount=40), ResponseTransfer)
assert done.source_balance == 60

try:
    treasury_bus(CommandTransfer(source="carla", target="dan", amount=500), ResponseTransfer)
    raise AssertionError("carla cannot give what she does not have")
except ConstraintViolation:
    pass
assert repository.get_by(Account, holder="dan").balance == 40     # the credit was undone too
```

**Opened explicitly, joined implicitly, and only on the same `Database`** — the propagation
Spring calls `REQUIRED`. A repository on another `Database` never joins: two engines share no
transaction, and that is the outbox's and the saga's job. A Feature called on its own still
commits on its own. `context(separate=True)` opens a transaction of its own even inside another —
an audit of the attempt that must outlive the rollback; it needs a database with a connection pool.
The unit of work belongs to its thread or task: a worker thread started inside the block does not
join it.

## 8. Numbers without gaps

A fiscal invoice's number has no holes. A database sequence is fast and never gapless —
`nextval` is not undone by a rollback — so `DatabaseNumbering` takes numbers from a counter row written in
the unit of work that saves what carries them: committed with them, or given back with them.

```python
from sincpro_framework.data_layer.orm import DatabaseNumbering, template_table

counters = template_table.numbering_table("numbering", treasury.metadata)
treasury.metadata.create_all(database.engine)
numbering = DatabaseNumbering(database, counters)

with repository.context():
    batch = numbering.take("F", count=3, scope="branch-1/2026")    # one write, three numbers
assert batch == range(1, 4)

try:
    with repository.context():
        numbering.take("F", count=2, scope="branch-1/2026")
        raise RuntimeError("the invoices failed")
except RuntimeError:
    pass

with repository.context():
    assert numbering.next_number("F", scope="branch-1/2026") == 4    # nothing was lost
```

**One statement, so there is no read to race**: `INSERT … ON CONFLICT DO UPDATE SET last = last +
n RETURNING last` on Postgres and SQLite, an `UPDATE` then an `INSERT` elsewhere. Two
transactions taking from one series wait for each other — the counter's row is held until the
first commits — so take the numbers last, keep that transaction short, and give each point of
sale or branch its own `scope`. Taken outside a unit of work a number commits on its own, and the
log says so: a failure before the invoice is saved leaves a gap. Gapless is the promise inside the
`context()` that saves what carries the number.
`MemoryNumbering` is the double for a test.

## 9. The passthrough

| Need | Door | What it keeps |
|---|---|---|
| pool, driver, connect arguments | `Database(url, **engine_options)` | everything |
| an option on this transaction | `context(engine={…})` | everything |
| a query a criteria cannot say | `statement()` → `run()` | the envelope, tracing |
| anything SQLAlchemy has | `unit.session` | the transaction, stamping, tracing — not hooks, `version` or the cascade |
