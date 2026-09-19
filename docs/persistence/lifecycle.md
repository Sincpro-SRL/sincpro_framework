# The lifecycle: what runs when something is written, and who it reaches

Three places can watch a write, and they are not interchangeable. What separates them is not
preference — it is **reach**: how much of the system each one can see.

| | Registered on | Reaches |
|---|---|---|
| the session | `Database.before_flush` / `after_flush` | **everyone** who writes through this database |
| the repository | `Hooks`, `Rule` | whoever goes through that repository |
| the aggregate | `pull_events()` | whoever holds the aggregate |

## Why reach decides where something goes

```python
with database.session() as session:      # no repository anywhere
    session.add(Client(name="ACME"))
```

That write is legal, ordinary, and a repository hook never sees it. So anything the framework
**guarantees** has to live where nothing can step around it:

- **Who wrote it** — `AuditedMixin`, stamped from the `Database`'s actor.
- **What changed** — `ChangeTrackingMixin`, taken from the engine's own record of what it is
  about to write.

Both are registered on the session, through `before_flush`, which is the moment that sees every
write. A rule on a repository could not be a guarantee, because a use case can simply not use
the repository.

## The order of one write

```
repository.save(invoice)
    │
    ├─ before_save(invoice)            the project's rules: validate, compute, refuse
    ├─ before_create / before_update   …the same write, told apart
    │
    ├─ session flush ──┬─ stamping     created_by / updated_by / updated_at
    │                  └─ tracking     one consolidated Updated event, recorded on the aggregate
    │                  └─ …and whatever else was given to before_flush
    │
    ├─ after_save(invoice)
    └─ after_create / after_update
                                       …and the block commits later
```

**A batch runs every before-moment first, then the writes, then every after-moment** — so a
batch refused halfway writes nothing and no after-moment ran. Both stores do this.

## What is not after the commit

`after_save` is not. Inside `context()` a save flushes and the block commits later, so a fact
announced from `after_save` is one a rollback can still take back.

There is no `after_commit` hook. What tells the world something happened lives **outside** the
write: the aggregate records events, the Feature pulls them once the unit of work has closed,
and publishes there.

```python
with self.repository.context() as ledger:
    entry.post()
    ledger.save(entry)
    recorded = entry.pull_events()

for event in recorded:            # committed by now
    self.publisher.publish(event)
```

That is also why an outbox is a table and not a hook: the fact and the intent to publish it
commit together, or neither does.

## The two change-tracking halves

The aggregate says **what counts as a change** (`tracked_fields()`, `change_event`). The store
says **when to look**, and the two stores cannot do it the same way:

- **SQLAlchemy** asks the engine at `before_flush`. Exact, and it sees every route to the
  database — including an aggregate loaded inside `context()`, modified, and flushed without
  anybody calling `save()`.
- **`MemoryRepository`** has no flush to ask, so it compares against a baseline taken when it
  handed the aggregate out. That is the approximation: a change nobody `save()`d records nothing
  there, and records on SQLAlchemy, because the row changed.

Everything else about the two stores is proved identical in
`tests/orm/test_lifecycle_parity.py`, which runs one script against both and compares.

## Taking the session moments yourself

```python
database.before_flush(lambda session: ...)   # still changeable, sees session.new / .dirty
database.after_flush(lambda session: ...)    # the statements have gone out, not committed
```

The same door the framework's own two use. Neither is where the world gets told: the
transaction has not committed and can still be undone.

## Where to put a thing

| What you want | Where |
|---|---|
| refuse a write that breaks an invariant | `before_save` on the repository |
| compute a derived field | `before_save` — the consolidated event sees it, because the store's bookkeeping closes the moment |
| stamp something on **every** write, repository or not | `Database.before_flush` |
| tell another service | pull the events after the unit of work, publish there |
| restrict what somebody may read | `narrowed()`, which the store cannot be talked out of |
| write to another aggregate | a Feature — a hook that writes is refused |
