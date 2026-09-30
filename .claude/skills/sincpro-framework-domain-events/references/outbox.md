# The transactional outbox

Use this when a fact crosses a process and must survive a crash between the write and the delivery.
The event is written **in the same transaction as the state change**; a separate relay reads what is
pending and publishes it. `EventTrackableMixin` adds the delivery state; `delivery_columns()` adds it
to the table. No new abstraction: an event is an `Entity`.

Full depth: `docs/events/change-tracking.md` §"An outbox", `tests/orm/test_outbox.py`.

## The table

```python
from datetime import datetime
from sqlalchemy import Column, DateTime, Integer, Text

from sincpro_framework.ddd import DomainEvent, EventStatus, EventTrackableMixin
from sincpro_framework.orm import entity_table, event_columns, map_aggregates


@dataclass(kw_only=True)
class InvoiceSent(EventTrackableMixin, DomainEvent):
    name = "billing.invoice.v1.sent"
    invoice_id: str = ""


class Outbox(EntityCollection[InvoiceSent]): ...


outbox_table = entity_table(
    "outbox", outbox.metadata,
    *event_columns(),                       # label(JsonText), entity_type, entity_id,
                                            # correlation_id, causation_id, sequence
    Column("invoice_id", Text),
    # The delivery state, declared to match EventTrackableMixin exactly:
    Column("status", Text, nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("error_message", Text),
    Column("acknowledged_at", DateTime(timezone=True)),
    Column("failed_at", DateTime(timezone=True)),
    Index("outbox_pending", "status"),
)
map_aggregates(outbox, {InvoiceSent: outbox_table})
```

> **Do not use `delivery_columns()` for this.** In 3.14.1 it emits `status, attempts, failure,
> delivered_at`, which do **not** match `EventTrackableMixin` (`error_message`, `acknowledged_at`,
> `failed_at`). Only `status` and `attempts` would persist; `mark_failed(reason)` and
> `mark_acknowledged()` would write fields with no column and lose them silently. Declare the five
> columns explicitly, as the framework's own `tests/orm/test_outbox.py` does. This mismatch is
> reported in `framework_gotchas`.

## The write and the fact commit together

```python
with repository.context() as unit:
    invoice = unit.get_by(Invoice, number="F-003")
    invoice.state = "sent"
    unit.save(invoice)
    unit.save(InvoiceSent(invoice_id=invoice.id))     # the same transaction
```

Either both landed or neither did. Publishing after the block commits is a window where a crash
loses the fact for good.

## The relay: claim, deliver, acknowledge

```python
PENDING = Criteria(where=Condition(field="status", value=EventStatus.PENDING))

with repository.context() as unit:                    # claim
    claimed = unit.search(Outbox, PENDING, for_update=True, skip_locked=True).items
    for one in claimed:
        one.mark_processing()
        unit.save(one)

for one in claimed:                                   # deliver
    queue.put(one)                                    # or publisher.publish(...)

with repository.context() as unit:                    # acknowledge
    for one in claimed:
        one.mark_acknowledged()
        unit.save(one)
```

`for_update=True, skip_locked=True` lets two relays run without ever taking the same row (on
PostgreSQL; SQLite has one writer anyway, so a test there proves the shape and not the exclusion —
run that one against Postgres). **Claims in `id` order**, or the relay delivers facts out of order.

## Failure and dead letters

- `one.mark_failed(reason)` records the failure; `attempts` caps the retries.
- After the cap, `mark_cancelled()` gives up and no later claim picks it up — a dead letter.
  Pinned in `tests/ecosystem/case_3_distributed`.

## What a relay needs (and what already answers it)

| Need | Answer |
|---|---|
| where it lives | the table you map the event to, in the `Database` you choose |
| delivery state | `EventTrackableMixin`: `status`, `attempts`, `error_message`, `mark_processing/acknowledged/failed` |
| claiming a batch | `search(..., for_update=True, skip_locked=True)` |
| finding fast | an `Index` and a `Criteria` |

The relay loop itself (~20 lines) is written by the project; no class ships it.
