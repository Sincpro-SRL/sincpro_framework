# Event sourcing: the facts are the state

Use this when rebuilding state from facts is a requirement. An audit trail beside an ordinary
aggregate is event storage, not event sourcing: keep the aggregate's row for that case.

## One event table per context

Map the context's base event once; its subclasses share the table. The wire `name` selects the
Python class, the envelope is columns, and subclass fields are JSON `payload`.

```python
from dataclasses import dataclass

from sqlalchemy.orm import registry

from sincpro_framework.ddd import DomainEvent, Entity, EventSourcedMixin
from sincpro_framework.data_layer.orm import map_events, template_table


@dataclass(kw_only=True)
class BankingEvent(DomainEvent):
        pass


@dataclass(kw_only=True)
class MoneyDeposited(BankingEvent):
        name = "bank.account.v1.money_deposited"
        amount: int = 0


@dataclass
class Account(EventSourcedMixin, Entity):
        event_base = BankingEvent
        balance: int = 0

        def apply(self, event: DomainEvent) -> None:
                match event:
                        case MoneyDeposited():
                                self.balance += event.amount

        def deposit(self, amount: int) -> None:
                self.happened(MoneyDeposited(amount=amount))


banking = registry()
events = template_table.event_table("banking_events", banking.metadata)
map_events(banking, BankingEvent, events)
```

Create the table through the project's migrations. **Do not map `Account` to a state table.**
Every state field needs a default: replay constructs `Account(id=identity)` before applying facts.

## Record, apply, append, rebuild

```python
account = Account(id="acc-1")
account.deposit(500)
account.deposit(300)
repository.save(account)

again = repository.get(Account, "acc-1")
assert (again.balance, again.version) == (800, 2)
```

`happened(event)` stamps the aggregate identity, assigns `entity_version = version + 1`,
records the event, applies it and advances the version. `save(account)` appends the recorded
events; `get(Account, identity)` reads `event_base` and folds the entity's events in
`entity_version` order. **Do not pull before saving:** that removes the facts awaiting storage.
An account with no stored events cannot be rebuilt.

`apply` is deterministic state mutation only: no I/O, publication, new events or clock reads.
Validate a command before `happened`; replay must still accept the facts already committed.

## Concurrent writers and history

The SQL event table makes `(entity_type, entity_id, entity_version)` unique. Two copies at
the same version cannot both append the next position: the loser raises `StaleAggregate`.
Reload and re-evaluate the command, not just the failed event. Test concurrent writers on the
real backend; the memory double does not prove database constraints or transaction behavior.

Ordinary row-backed aggregates use `record`, not `happened`; their events have no numbered
stream by default (`entity_version=None`). Do not manufacture stream versions from old audit
rows unless their completeness and ordering have been established.

## Limits to preserve

- The normal save path appends new events; it is not a database-wide immutability policy.
    Restrict direct event updates/deletes separately. A blanket no-update hook on all events
    would also prevent a relay from saving delivery metadata.
- No automatic snapshots or projection rebuild worker ships here. `get` replays the history;
    use ordinary row-backed read models for listing and analysis when needed.
- Subclass payload fields are JSON, not individually mapped columns. Do not assume an
    `amount` payload field supports ordinary column filters or `measures`.
- Version wire names for breaking shapes. Keep historical classes imported and mappable
    while their rows exist; retaining JSON alone does not let the polymorphic ORM load an
    unknown discriminator. Plan an explicit data migration before removing a class.
- A persisted event is not automatically deliverable. Add `DeliverableEventMixin` only to
    events a relay must send; see [outbox.md](outbox.md).

Framework evidence: `docs/persistence/guide.md` sections 10-12,
`tests/data_layer/orm/test_events_table.py`, `tests/event_driven/test_relay.py`.
