# Recipe: drafts between requests — `InMemoryDrafts` / `KeyValueDrafts`

Use it when a form keeps what the user typed across requests before saving: a long edit, a
wizard, the same record open in two tabs. If others must see the draft, make it a stored record
with `state=DRAFT` instead — nothing from this recipe is needed for that.

## 1. Register a store as a dependency

```python
from datetime import timedelta
from sincpro_framework.caching.adapters.redis import RedisKeyValue
from sincpro_framework.ddd.drafts import InMemoryDrafts, KeyValueDrafts

billing.add_dependency("drafts", KeyValueDrafts(RedisKeyValue(redis), ttl=timedelta(days=7)))
# tests: InMemoryDrafts(ttl=timedelta(hours=1), now=clock.now)
```

## 2. Keep and read, naming the version read

```python
key = f"invoice:{invoice_id}:{user_id}"                 # whose draft of what — the caller's key
draft = self.drafts.read(key)                           # Draft | None
expected = draft.version if draft else 0
draft = self.drafts.keep(key, dto.values, origin_version=invoice.version, expected=expected)
```

`DraftConflict` (`current` = version there now) when another tab kept first: return it to the
client (409 via `FailureKind.CONFLICT`), which reads again and merges. Never retry blindly.

## 3. Activate in a Command

```python
from sincpro_framework.ddd import assign

invoice = self.repository.get(Invoice, invoice_id)
refuse_stale(draft, invoice)          # StaleAggregate if saved since the draft started
assign(invoice, draft.values)         # values are JSON: each read as its field's type
self.repository.save(invoice)
self.drafts.discard(key)
```

## Mistakes to avoid

- **Setting `invoice.version = draft.origin_version` to get the version check.** It does not
  check anything on SQL — the mapping writes it as a new value. Call `refuse_stale`.
- **Passing `expected=0` always.** It conflicts with any live draft; pass the version you read.
- **A key without the user** when each user should have their own draft.
- **Using drafts for what others must see** (a draft invoice for approval): that is a stored
  record with a state.
- **Treating `values` as typed.** They are JSON (`"1150.50"`, `"2026-11-01"`); put them on the
  record with `assign`, never `setattr`: a `str` where a `Decimal` belongs breaks the save.
- **Counting with `version`.** It is opaque, like an ETag, and never repeats for a key — not
  after a discard, not after an expiry. Compare it and hand it back as `expected`; nothing more.
