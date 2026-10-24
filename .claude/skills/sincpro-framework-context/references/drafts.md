# Recipe: drafts between requests — `use_context().drafts()`

Use it when a form keeps what the user typed across requests before saving: a long edit, a
wizard, the same record open in two tabs. A draft is a context kept by whose it is, where the
context is kept. If others must see the draft, make it a stored record with `state=DRAFT`
instead — nothing from this recipe is needed for that.

A screen that only needs keep / save / discard / find again uses the built-in `Edit` command over
these drafts — skill `sincpro-framework-persistence`, recipe `references/edits.md`. This recipe
is for a Feature that handles drafts itself.

## 1. Keep the context somewhere — once

```python
from sincpro_framework.context import KeyValueContexts, InMemoryContexts
from sincpro_framework.store.adapters.redis import RedisKeyValue

billing.store_context_in(KeyValueContexts(RedisKeyValue(redis)))   # production: every replica
billing.store_context_in(InMemoryContexts())                        # one process, tests
```

Nothing else to register: the context store keeps the drafts beside the contexts, each lasting
`entry_ttl` (seven days) after its last keep.

## 2. Keep and read, naming the version read

```python
drafts = self.context.drafts()                          # the caller's — by user, within the tenant
subject = f"invoice:{invoice_id}"                       # what the draft is about, nothing more
draft = drafts.read(subject)                            # Draft | None
expected = draft.version if draft else 0
draft = drafts.save(subject, dto.values, origin_version=invoice.version, expected=expected)
```

Whose it is comes from the context: `drafts()` is the user's; `drafts(by=["session_id"])` a
session's; `drafts(by=["user_id", "pos_id"])` any keys. A tenant in the context always takes part.
A key `by` names that the context lacks is `ContextRequired`.

`DraftConflict` (`current` = version there now) when another tab kept first: return it to the
client (409 via `FailureKind.CONFLICT`), which reads again and merges. Never retry blindly.

## 3. Activate in a Command

```python
from sincpro_framework.ddd import assign, refuse_stale

invoice = self.repository.get(Invoice, invoice_id)
refuse_stale(draft, invoice)          # StaleAggregate if saved since the draft started
assign(invoice, draft.values)         # values are JSON: each read as its field's type
self.repository.save(invoice)
drafts.discard(subject)
```

## Mistakes to avoid

- **Putting the user in the subject** (`f"invoice:{id}:{user_id}"`). The context already says
  whose it is; the subject says what it is about.
- **Registering a draft store as a dependency.** The context's store keeps them.
- **Setting `invoice.version = draft.origin_version` to get the version check.** It does not
  check anything on SQL — the mapping writes it as a new value. Call `refuse_stale`.
- **Passing `expected=0` always.** It conflicts with any live draft; pass the version you read.
- **Using drafts for what others must see** (a draft invoice for approval): that is a stored
  record with a state.
- **Treating `values` as typed.** They are JSON (`"1150.50"`, `"2026-11-01"`); put them on the
  record with `assign`, never `setattr`.
- **Counting with `version`.** It is opaque, like an ETag, and never repeats for a key.
