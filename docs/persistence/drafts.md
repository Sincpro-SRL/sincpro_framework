# Drafts: what a form keeps between requests

A form that takes more than one request — a long invoice, a wizard, a cart, the same record
open in two tabs — keeps what the user typed somewhere before it is saved. `Drafts` is that
place: optional, optimistic, and always with a TTL.

```python
from datetime import timedelta

from sincpro_framework.caching.adapters.redis import RedisKeyValue
from sincpro_framework.ddd.drafts import InMemoryDrafts, KeyValueDrafts

drafts = KeyValueDrafts(RedisKeyValue(redis), ttl=timedelta(days=7))   # every replica
drafts = InMemoryDrafts(ttl=timedelta(hours=1))                        # one process, tests
```

| Call | What it does |
|---|---|
| `drafts.keep(key, values, origin_version, expected)` | keeps `values` under a new version, when `expected` is the version there now; `Draft` back — the one `read` gives until the next keep |
| `drafts.read(key)` | the live `Draft`, or `None` (none, discarded, expired) |
| `drafts.discard(key)` | drops it; a key with none is not an error |
| `refuse_stale(draft, record)` | `StaleAggregate` when the record changed since the draft started |

A `Draft` carries `key`, `values` (as JSON carries them: `"1150.50"` for a decimal, ISO text for
a date), `version`, `origin_version` and `kept_at`. A version is opaque, like an ETag: compare
it, hand it back as `expected`, never count with it.

## Which draft

| | A draft here | A stored record with `state=DRAFT` |
|---|---|---|
| Who sees it | whoever holds its key | everybody who can read the record |
| Survives | its TTL | until deleted |
| Is a record | no: values a form typed | yes: it has an id, a number may wait for confirmation |
| Example | an edit in progress, a wizard step, two tabs | Odoo's draft invoice, Frappe's `docstatus=0` |

The second needs nothing from this module: it is a state of the aggregate.

## The key

The key is the caller's, and says whose draft of what: `invoice:{invoice_id}:{user_id}` for an
edit, `invoice:new:{user_id}:{form_id}` for a record not created yet. A key that leaves the user
out is one draft shared by everybody editing that record — sometimes what is wanted.

## Versions and conflicts

Every keep names the version it read: `0` when there is no live draft, `draft.version`
afterwards. When another keep came first — another tab, another device — the keep raises
`DraftConflict` with `current`, the version there now. Nothing is overwritten in silence and
nothing is locked: the losing tab reads again, merges or reloads, and keeps with that version.

```python
draft = drafts.read(key)                       # tab B reads the draft
drafts.keep(key, values_b, origin, expected=draft.version)   # tab A kept meanwhile
# DraftConflict: draft … is at version <A's>, not <the one B read>
```

`DraftConflict` declares `FailureKind.CONFLICT`, so every wire answers it as a conflict — 409
over REST; an API that takes the version in `If-Match` answers the same failure as its 412.

A version never repeats for a key — not after a discard, not after an expiry — so a tab still
holding an old one can never bring a dropped draft back, nor overwrite a draft another tab
started after the old one expired.

## The TTL

Every draft has one, given to the store, and every keep starts it again: an abandoned draft
disappears on its own and the store never grows without end. Days for an edit people come back
to, minutes or hours for a wizard.

## Activating a draft

Saving a draft is the project's Command. The draft never becomes the record by itself:

```python
@billing.feature(CommandActivateInvoiceDraft)
class ActivateInvoiceDraft(Feature):
    def execute(self, dto: CommandActivateInvoiceDraft) -> None:
        key = f"invoice:{dto.invoice_id}:{self.context['user_id']}"
        draft = self.drafts.read(key)
        if draft is None:
            raise DraftConflict(key, dto.version, 0)
        invoice = self.repository.get(Invoice, dto.invoice_id)
        refuse_stale(draft, invoice)                # somebody saved it since: StaleAggregate
        assign(invoice, draft.values)               # JSON read as each field's type
        self.repository.save(invoice)
        self.drafts.discard(key)
```

`refuse_stale` compares the record's `version` with the draft's `origin_version` before the
draft's values touch the record. `assign` (`sincpro_framework.ddd`) reads each JSON value as its
field's type — `"1150.50"` becomes a `Decimal` — so the save sees what a typed edit would. Setting `version` back on a loaded record is not a check: the
mapping writes it as a new value and the save goes through.

## How the stores keep it

`InMemoryDrafts` is a dict behind a lock, for one process and for tests. It remembers a key's
last version for a TTL after its draft was last kept or discarded, then forgets it, so it does
not hold every key it ever saw; a chain started after that begins at the clock's milliseconds,
and always above any version the store handed out for any key — so a version never repeats,
not under a clock that stands still, not with a TTL shorter than the keeps it holds.

`KeyValueDrafts` stands on any `KeyValueStore` — Redis, Valkey, Memcached — with nothing
store-specific. A draft is a chain of slots, `{prefix}:{key}:{version}`, each written once with
the store's atomic `add`: of two keeps naming the same version, exactly one writes the next
slot. A slot is never deleted — its TTL removes it — so its number is never handed out again; a
discard writes the next slot as spent. Two pointers, `{prefix}:{key}:head` and
`{prefix}:{key}:chain`, say where the chain was last seen; a reader takes the larger and probes
the slots after it, so a keep that won its slot and had not moved them yet is still read, and
both are written again. The store evicting one of them, or a process stopping between its slot
and them, loses nothing. Every key carries the TTL.

When nothing of a chain is left — every key expired — the next keep starts it again at the
clock's milliseconds, above anything a tab could still hold, and only one of the keeps that
found nothing may start it: they meet on a start mark, `{prefix}:{key}:start:{last seen}`,
written with `add`, holding where the new chain begins and when it was started. A reader that
finds the mark and its first slot follows it, so a draft whose pointers were never written is
still read. A mark with no slot yet is a keep still writing while it is younger than
`RESTART_WINDOW` (ten seconds) — the other keeps lose — and a keep that stopped once it is
older: its first slot is spent as `dead` before anything is built on it, so a keep that was
only slow, or ran on a clock that far off, loses its `add` there with `DraftConflict` instead
of writing a draft nobody reaches. Pointers only move forward. With every pointer and mark
evicted, a draft whose slots survive is no longer found; the next keep starts a new chain and
never takes a living slot.

**What the clocks decide.** Correctness does not depend on them: no keep is answered and then
lost, and no two drafts share a version, whatever the clocks of the replicas say — every slot
is won with `add`. The clocks decide two things only: liveness — a start slower than
`RESTART_WINDOW` loses and is kept again by the caller — and that a chain started over a fully
expired one does not reuse its numbers, which holds while the replicas' clocks agree to within
the TTL.
