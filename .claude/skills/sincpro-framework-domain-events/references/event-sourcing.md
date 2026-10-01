# Event sourcing — the facts are the state

Use this only when the history **is** the product: a ledger, an audit, a state machine that only
moves forward and that somebody has actually asked to replay. Event sourcing by default costs
projections, snapshots, event versioning and a migration story nobody asked for.

Deeper, in the framework repo: `docs/shapes.md` §3, `docs/persistence/guide.md` §11,
`tests/orm/test_event_store.py`.

## Writing is appending

Nothing is updated. A `DomainEvent` is an `Entity`, so the repository that already exists stores and
queries it. `event_columns()` is the envelope every event carries.

```python
from dataclasses import dataclass

from sqlalchemy import Column, Integer, Text
from sqlalchemy.orm import registry

from sincpro_framework.ddd import Criteria, Condition, DomainEvent, EntityCollection, Sort
from sincpro_framework.orm import entity_table, event_columns, map_aggregates

ledger = registry()


@dataclass(kw_only=True)
class MoneyDeposited(DomainEvent):
    name = "bank.v1.money_deposited"
    account_id: str = ""
    amount: int = 0


class Deposits(EntityCollection[MoneyDeposited]): ...


deposited_t = entity_table(
    "deposited", ledger.metadata, *event_columns(),
    Column("account_id", Text), Column("amount", Integer),
)
map_aggregates(ledger, {MoneyDeposited: deposited_t, ...})
```

```python
repository.save(AccountOpened(account_id=account, holder="Ana"))
repository.save([MoneyDeposited(account_id=account, amount=500),
                 MoneyDeposited(account_id=account, amount=300)])
repository.save(MoneyWithdrawn(account_id=account, amount=200))
```

## Rebuilding is folding the facts

```python
mine = Criteria(where=Condition(field="account_id", value=account), order=(Sort(field="id"),))
balance = (sum(one.amount for one in repository.fetch_all(Deposits, mine))
           - sum(one.amount for one in repository.fetch_all(Withdrawals, mine)))
```

**Ordering by `id` is ordering by time** — an id is a UUID v7.

Rebuilding on every read does not scale, so the answer is usually kept beside the facts as a
**projection** — an ordinary aggregate, saved like any other:

```python
repository.save(Balance(account_id=account, holder="Ana", amount=balance))
```

The history stays queryable with the same `Criteria`: `repository.measures(Deposits, mine,
total=("sum", "amount"))`.

## Append-only is a discipline, not a store mode

A four-line hook enforces it (`sincpro-framework-persistence` → hooks):

```python
from sincpro_framework.ddd import ContractViolation, Hook, Hooks
from sincpro_framework.orm import Repository

append_only = Hooks(None)

@append_only.on(DomainEvent)
class WrittenOnce(Hook):
    def before_save(self, fact: DomainEvent) -> None:
        if not fact.is_new:
            raise ContractViolation(f"{fact.name} is written once and never replaced")

facts_only = Repository(database, append_only)
```

## What this shape costs

- **The facts are forever.** A field added today is missing from every row written before it, so the
  event's shape is versioned in its `name` (`bank.v1.…`); a v2 is a new class.
- **There is no "just fix the row".** A correction is another fact.
- Append-only is a discipline, not something the store enforces.

## The event store needs no `EventRepository`

`DomainEvent` inherits `Entity` (id, `created_at`, `version`), so the existing repository persists and
queries it. Map your event class to a table and use `save` / `search` / `get`. Written in the same
unit of work that changed the row, the event and the change commit or roll back together — which is
the whole point of an outbox, without a second abstraction.
