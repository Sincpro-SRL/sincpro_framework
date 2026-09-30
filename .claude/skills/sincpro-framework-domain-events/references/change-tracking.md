# Change tracking

`ChangeTrackingMixin` records **one** `EntityUpdated` event per save, with every field that changed
as `(before, after)`. Opt-in on the aggregate: one that does not inherit it pays nothing.

```python
from dataclasses import field
from sincpro_framework.ddd import ChangeTrackingMixin, Entity, EntityUpdated


@dataclass
class Invoice(ChangeTrackingMixin, Entity):
    number: str = field(metadata={"label": {"default": "Number", "es": "Número"}})
    state: str = "draft"
    render_cache: str = field(default="", metadata={"tracked": False})


invoice = repository.get(Invoice, invoice_id)
invoice.state = "posted"
repository.save(invoice)                       # the event is recorded here

[updated] = invoice.pull_events()
assert updated.changes == {"state": ("draft", "posted")}
```

A field set twice and landed back on its original value nets to no change: what is compared is the
value the aggregate was read with against the value it is saved with, never each assignment along
the way.

## What is tracked

Every field the aggregate declares, except:

| Not tracked | Why |
|---|---|
| `id`, `created_at`, `updated_at`, `version`, `created_by`, `updated_by`, `archived_at` | `Entity` and its mixins own them |
| `field(metadata={"tracked": False})` | the field says so |
| a field pointing at another `Entity` (to-one or to-many) | it is another aggregate's own `Updated`; tracking it would embed whole records |

A **value object is tracked** — it is part of this aggregate's state. The domain tells the two
apart by what the annotation inherits, never by whether an ORM maps it.

## Where the diff comes from

- **On SQLAlchemy**, the engine already keeps the loaded and pending value, so the diff is read at
  `before_flush`. It is registered on the **session**, not the repository — so it covers every route
  to the database, including a plain session or a script.
- **`MemoryRepository`** has no flush to ask: it takes a baseline when it hands an aggregate out and
  compares on `save()`. A change to an aggregate nobody `save()`d records nothing there and records
  on SQLAlchemy. Write the test knowing which of the two you are proving.

## Explicit mode

```python
with self.repository.context() as unit:
    invoice.post()
    event = unit.record_changes(invoice)       # settled here, and handed over
if event is not None:
    self.events.save(event)
    self.publisher.publish(event)
```

Returns `None` when nothing differs, or when the aggregate was never stored (a first save is a
Created fact, not this). **Publish one or the other, never both** — it is the same event
`pull_events()` will hand over.

## A custom `Updated` event and the words

```python
@dataclass(kw_only=True)
class InvoiceUpdated(EntityUpdated):
    name = "billing.invoice.v1.updated"
    label: dict[str, str] = field(default_factory=lambda: {"default": "Updated", "es": "Se actualizó"})

@dataclass
class Invoice(ChangeTrackingMixin, Entity):
    change_event = InvoiceUpdated
```

`changes` is enough for a program; the event also carries the words a person reads:
`event.label`, `event.entity_type`, `event.field_labels` (only the fields that moved). The words
travel with the event on purpose: an audit says what a person saw at the time, and an event that
crossed a service has no model there to ask.

## Correlating a chain: `caused_by`

```python
for event in invoice.pull_events():
    self.publisher.publish(event.caused_by(incoming))
```

`causation_id` becomes the cause's id; `correlation_id` the one the cause carried (or the cause's id
when it carried none). It is not filled in automatically because a bus's context is per bus
instance — an aggregate belongs to no bus. Thread the chain where both ends are known.

## Traps

- A tuple has no JSON: `changes={"state": ("a","b")}` comes back as a list.
- A `StrEnum` in a `Text` column comes back as a plain string.
- **Never map the framework's generic `EntityUpdated`** — mapping it drags every subclass into your
  table. Map your own subclass.
- Archiving emits nothing (`archived_at` is not tracked). `remove` deletes the row.
- The event is recorded **before the commit lands**; a rollback leaves the aggregate believing it is
  unchanged — read it again rather than retrying with the object in hand.

Full detail: `docs/events/change-tracking.md`.
