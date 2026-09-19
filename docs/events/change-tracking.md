# Change tracking: one `Updated` event with everything that changed

```python
@dataclass
class Invoice(ChangeTrackingMixin, Entity):
    number: str = field(metadata={"label": {"default": "Number"}})
    state: str = "draft"
    render_cache: str = field(default="", metadata={"tracked": False})

invoice = self.repository.get(Invoice, invoice_id)
invoice.state = "posted"
invoice.number = "F-0042"
self.repository.save(invoice)

invoice.pull_events()
# [EntityUpdated(changes={"state": ("draft", "posted"), "number": ("F-1", "F-0042")})]
```

One event, not one per field a Feature set. A field set twice and landed back on its original
value nets to no change: what is compared is the value the aggregate was read with against the
value it is saved with, never each assignment along the way.

**Opt-in on the aggregate.** One that does not inherit `ChangeTrackingMixin` pays nothing and
never knows this exists.

## The pieces

| Piece | Module | What it is |
|---|---|---|
| `ChangeTrackingMixin` | `ddd/entity/mixins/tracking.py` | What counts as a change, and what event to emit |
| `EntityUpdated` | `ddd/entity/mixins/tracking.py` | The generic event: `changes: {field: (before, after)}`, plus `label` and `field_labels` — the words a person reads |
| `_tracking` | `orm/sqlalchemy/change_tracking.py` | On SQLAlchemy: the diff the engine is about to write, at `before_flush` |
| `ChangeTrackingRepositoryMixin` | `ddd/repositories/change_tracking.py` | On a store with no flush: a baseline on the way out, the diff on `save()` |
| `EventTrackableMixin` | `ddd/events.py` | Delivery state for an event that goes to an outbox |

## Where the diff comes from, and why it is not the same in both stores

**On SQLAlchemy, the engine already knows.** It keeps the loaded value and the pending one for
every column it is about to write, so the diff is read off `get_history` at `before_flush` —
exact, and for every route that reaches the database.

**It is registered on the session, not on the repository, and that is the point.** A rule on a
repository runs for whoever goes through that repository. This runs for whoever writes through
this `Database`: a use case holding a plain session, a script, a migration. What the framework
*guarantees* has to live where nothing can step around it — the same reason the `created_by`
stamping is registered there.

```python
with repository.context() as unit:
    invoice = unit.get(Invoice, invoice_id)
    invoice.total = 900          # no save(): the session already knows, and so does the event
```

**`MemoryRepository` has no flush to ask**, so it takes a baseline when it hands an aggregate
out and compares on `save()`. That is the approximation, and it is honest about its limit: a
change to an aggregate nobody `save()`d records nothing there, and records on SQLAlchemy,
because the row changed. Write the test against the double knowing which of the two you are
proving.

## What is tracked

Every field the aggregate declares, except three kinds:

| Not tracked | Why |
|---|---|
| `id`, `created_at`, `updated_at`, `version`, `created_by`, `updated_by`, `archived_at` | `Entity` and its mixins already own them |
| `field(metadata={"tracked": False})` | the field itself says so |
| `author: Author \| None`, `runs: list[Run]` | another aggregate — see below |

```python
render_cache: str = field(default="", metadata={"tracked": False})   # never in the diff
```

`Invoice.tracked_fields()` answers the set, read once per class off the dataclass.

**A field pointing at another `Entity` is never tracked**, to-one or to-many. It is not a value
of this aggregate, it is a different one: a change there is that aggregate's own `Updated`
event. Tracking it would also put whole records inside the event — every column of every
related row, growing with the relation.

**A value object is tracked**, because it *is* part of this aggregate's state:

```python
size: Shape = field(default_factory=Shape)
# changes == {"size": (Shape(width=1), Shape(width=9))}
```

The domain layer tells the two apart by what the annotation inherits — an `Entity` is another
aggregate, a plain dataclass is a value — never by whether an ORM happens to map it.

## The two modes

### Automatic — the repository does it

`save()` consolidates whatever changed into one event and records it. Nothing else to call:

```python
invoice.state = "posted"
self.repository.save(invoice)          # the event is recorded here
for event in invoice.pull_events():    # the Feature still decides what to do with it
    self.publisher.publish(event)
```

### Explicit — you do it, and get the event back

```python
event = invoice.record_changes()
if event is not None:
    self.events.save(event)            # store it
    self.publisher.publish(event)      # or hand it to a queue
```

`record_changes()` answers `None` when nothing differs, and when the aggregate has never been
saved — a first save is a Created fact, written by hand, not this.

**What it returns is the same event `pull_events()` will hand over**, not a second one:
publish one or the other, never both. Either way the baseline moves to the current state, so
the next cycle compares from there.

The automatic mode *is* this call: the repository's `before_save` is one line, `record.record_changes()`.

### A domain event of your own

```python
@dataclass(kw_only=True)
class InvoiceUpdated(EntityUpdated):
    name = "billing.invoice.v1.updated"

@dataclass
class Invoice(ChangeTrackingMixin, Entity):
    change_event = InvoiceUpdated
```

## What a person reads when the event is opened

`changes` is field names and raw values — enough for a program, not for a person. So the event
also carries the words:

```python
@dataclass
class Invoice(ChangeTrackingMixin, Entity):
    total: int = field(default=0, metadata={"label": {"default": "Total", "es": "Importe"}})
    memo: str = ""
```

```python
event.label         # {"default": "Updated", "es": "Se actualizó"}
event.entity_type   # "Invoice"
event.field_labels  # {"total": {"default": "Total", "es": "Importe"}}
event.changes       # {"total": (100, 500)}
```

`label` is `DomainEvent`'s own field, which every event has — `EntityUpdated` just gives it a
default instead of leaving it empty. `entity_type` already says *what* was updated, so the
sentence does not repeat it. **Spanish says "Se actualizó" and not "Actualizado"**: an
adjective would have to agree with a noun the event does not know the gender of.

`field_labels` holds **only the fields that moved** — a change to one field does not drag the
whole model's vocabulary along — and a field that declared no label is simply absent rather
than empty.

### Why the words are carried, not looked up later

Two reasons, and the second is the one that matters:

- An event that crossed to another service has no model class there to ask.
- **An audit says what a person saw at the time.** Rename the label next year and the old
  entries have to keep the old word, or the record has been rewritten.

### Saying it in your own words

A domain event sets its own default `label`, the same way `EntityUpdated` does:

```python
@dataclass(kw_only=True)
class InvoiceSettled(EntityUpdated):
    name = "billing.invoice.v1.settled"
    label: dict[str, str] = field(
        default_factory=lambda: {"default": "Settled", "es": "Se canceló"}
    )
```

## Correlating a chain: `caused_by`

An event the automatic mode recorded carries no `correlation_id` — the aggregate has no
ambient request to read from. Whoever holds both events threads the chain, in one call:

```python
for event in invoice.pull_events():
    self.publisher.publish(event.caused_by(incoming))
```

`causation_id` becomes the cause's own id — what directly led here — and `correlation_id` the
one the cause already carried, or the cause's id when it carried none, because an event nobody
correlated *is* the head of its chain. Three services later, one `correlation_id` names the
whole thing and the `causation_id`s put it in order. A new event comes back; the cause is
untouched.

**Why it is not filled in automatically.** A bus's context is a `ContextVar` created *per bus
instance* (`context/mixin.py`), never process-wide — deliberately, because a process runs
several buses and an aggregate belongs to none of them. There is no ambient request an
`Entity.record()` could reach for, and inventing a global one to fake it would be wrong the
moment a second bus exists. The chain is threaded where both ends are actually known.

For the head of a chain — a command, not an event — the Feature already has the request's own
correlation: `self.context.get("correlation_id")`.

## One trace across the process boundary

`BackgroundQueue` sends three things, not two: the event's name, its payload, and the trace it
was published under as a W3C carrier. The worker adopts that carrier before handing the event
to its buses, so what the consumer does lands under the span that published instead of starting
a trace of its own.

```python
# producing side, inside whatever span is running
queue.put(event)            # (name, payload, {"traceparent": "00-…"})

# worker side, before the buses see it
with _adopted(carrier):
    subscriber.handle(event)
```

Nothing is added to `DomainEvent`: a trace is about the call that produced the fact, not part
of the fact, so it rides beside the payload. With no span running, or with OpenTelemetry not
installed, the carrier is empty and everything behaves as before.

This is the same adoption `entrypoints/rpc/entrypoint.py` does for an incoming `traceparent`
header — the synchronous boundary — applied to the asynchronous one.

**A broker is a different boundary.** Kafka, RabbitMQ and the rest each have an official
OpenTelemetry instrumentation that propagates context through their own message headers; check
for it before writing this by hand for a new transport.

## Storing the events: no `EventRepository` class exists, and none is needed

`DomainEvent` inherits `Entity` — it has an `id`, a `created_at` and a `version` — so the
repository that already exists persists and queries it like any other aggregate. Map your event
class to a table and use `save` / `search` / `get`:

```python
event_table = entity_table(
    "domain_event", metadata,
    Column("label", JsonText, nullable=False),
    Column("entity_type", Text, nullable=False),
    Column("entity_id", Text, nullable=False),
    Column("correlation_id", Text),
    Column("causation_id", Text),
    Column("sequence", Integer, nullable=False),
    Column("changes", JsonText, nullable=False),
    Index("event_by_record", "entity_type", "entity_id"),
)
map_aggregates(registry, {InvoiceUpdated: event_table})

for event in invoice.pull_events():
    self.repository.save(event)

history = self.repository.search(
    InvoiceUpdates,
    Criteria(where=Condition(field="entity_id", value=invoice.id)),
)
```

Written in the same unit of work that changed the row, the event and the change commit or roll
back together — which is the whole point of an outbox, without a second abstraction.

See `tests/orm/test_event_store.py`.

## An outbox, with what is already here

Add `EventTrackableMixin` to the event and the row carries its own delivery state:

```python
@dataclass(kw_only=True)
class OrderUpdated(EventTrackableMixin, EntityUpdated):
    name = "sales.order.v1.updated"
```

| What a relay needs | What answers it |
|---|---|
| where it lives | the table you map the event to, in the `Database` you choose |
| the delivery state | `EventTrackableMixin`: `status`, `attempts`, `error_message`, and `mark_processing()` / `mark_acknowledged()` / `mark_failed()` |
| claiming a batch | `search(..., for_update=True, skip_locked=True)` — two relays never take the same row |
| finding one fast | an `Index` on the table, and `Criteria` to ask |

The relay itself, whole:

```python
with repository.context() as unit:
    claimed = unit.search(Outbox, PENDING, for_update=True, skip_locked=True)
    for event in claimed:
        event.mark_processing()
        unit.save(event)

for event in claimed:
    queue.put(event)

with repository.context() as unit:
    for event in claimed:
        event.mark_acknowledged()
        unit.save(event)
```

See `tests/orm/test_outbox.py` — the claim, the acknowledgement and the failure path.

## What to know before it bites you

- **A tuple has no JSON of its own.** `changes={"state": ("draft", "posted")}` comes back from
  a JSON column as `{"state": ["draft", "posted"]}` — a list. Compare accordingly.
- **A `StrEnum` in a `Text` column comes back as a plain string.** `status == EventStatus.PENDING`
  is true; `status is EventStatus.PENDING` is not.
- **A value object in `changes` needs a column that can serialize it.** `event.as_json()`
  handles it — pydantic walks the nested dataclass — but `JsonText` is a plain `json.dumps` and
  raises on it. Store such an event through a column type that serializes the way
  `Entity.as_json` does, or exclude the field with `tracked: False`.
- **Never map the framework's generic `EntityUpdated`.** Mapping a class makes every subclass
  of it part of that mapper's hierarchy, so another event class declared anywhere in the
  process gets dragged into your table. Map your own subclass.
- **Archiving emits nothing.** `archived_at` belongs to `ArchivableMixin`, so it is not a
  tracked field: `repository.archive(record)` changes the row without producing an `Updated`
  event. Record that fact by hand if a screen needs it. (`repository.remove(record)` deletes the
  row; there is nothing left to have changed.)
- **The event is recorded before the commit lands.** It is taken at the flush, or at `save()`
  on a store with no flush, not after the transaction commits. A rolled-back transaction
  leaves the aggregate believing it is unchanged; read it again rather than retrying with the
  object in hand.

## Not built yet

- **The actor inside the event — queued, and it depends on something else first.** An
  `EntityUpdated` does not say *who* made the change. Two reasons, and only the second is
  about this feature: there is no global context a use case reads the acting user from yet,
  and `updated_by` is stamped by the session's `before_flush`, which runs *after* `save()`
  built the event.

  **The guideline for whoever closes it:** the actor is never read off the aggregate at the
  moment the event is built — `updated_by` is not set yet and never will be in time. It is
  read from the same `actor()` callable the `Database` was already given
  (`Database(url, actor=lambda: bus.current_context().get("user.id"))`), which is the one place that
  answers "who is writing" for every path, including a raw session. When the global context
  lands, that callable is what it feeds; nothing in the tracking hook has to learn where the
  user came from.
- **The relay as a shipped component.** The loop above is twenty lines a project writes; no
  class ships it.
- **Trace across the asynchronous boundary — done.** See below.
- **Accumulating across transactions.** One consolidated event for a flow that spans several
  saves needs a scope somebody opens and closes; nothing here does that.
