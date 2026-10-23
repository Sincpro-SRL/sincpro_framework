# Drafts: what a form keeps between requests

A form that takes more than one request — a long invoice, a wizard, a cart, the same record
open in two tabs — keeps what the user typed somewhere before it is saved. A draft is a context
kept by whose it is: the context says whose, and keeps it where it keeps everything else — the
process's memory, or the Redis of production. Optional, optimistic, and always with a TTL.

```python
from sincpro_framework.context import KeyValueContexts
from sincpro_framework.data_layer.caching.adapters.redis import RedisKeyValue

billing.context_store(KeyValueContexts(RedisKeyValue(redis)))   # once: where the context is kept

drafts = self.context.drafts()                     # in a Feature — or use_context().drafts()
drafts = self.context.drafts(by=["session_id"])    # a session's instead of a user's
```

| Call | What it does |
|---|---|
| `drafts.keep(subject, values, origin_version, expected)` | keeps `values` under a new version, when `expected` is the version there now; `Draft` back — the one `read` gives until the next keep |
| `drafts.read(subject)` | the live `Draft`, or `None` (none, discarded, expired) |
| `drafts.discard(subject)` | drops it; a key with none is not an error |
| `refuse_stale(draft, record)` (`sincpro_framework.ddd`) | `StaleAggregate` when the record changed since the draft started |

A `Draft` carries `key`, `values` (as JSON carries them: `"1150.50"` for a decimal, ISO text for
a date), `version`, `origin_version` and `kept_at`. A version is opaque, like an ETag: compare
it, hand it back as `expected`, never count with it.

A form's whole life — keep, save, discard, find again — is one built-in command over these drafts:
[Edits](../persistence/edits.md).

## Which draft

| | A draft here | A stored record with `state=DRAFT` |
|---|---|---|
| Who sees it | whose it is: the user, the session — the context says | everybody who can read the record |
| Survives | its TTL | until deleted |
| Is a record | no: values a form typed | yes: it has an id, a number may wait for confirmation |
| Example | an edit in progress, a wizard step, two tabs | Odoo's draft invoice, Frappe's `docstatus=0` |

The second needs nothing from this module: it is a state of the aggregate.

## Whose it is

The caller names only what the draft is about — `invoice:{invoice_id}` for an edit,
`invoice:new:{form_id}` for a record not created yet. Whose it is comes from the context of the
call, never from that name, so two users editing the same invoice never share a draft:

| `drafts(...)` | Whose | Owner |
|---|---|---|
| `drafts()` | the user | `tenant_id=acme,tenant_ids=,user_id=ana` |
| `drafts(by=["session_id"])` | the session | `tenant_id=acme,tenant_ids=,session_id=s-1` |
| `drafts(by=["user_id", "pos_id"])` | the user at one point of sale | `tenant_id=,tenant_ids=,user_id=ana,pos_id=4` |

The owner always has the same shape: the tenant keys, then the keys of `by` — the user by
default — each with what the context says, and nothing when it says nothing.

A tenant in the context always takes part: a draft never crosses tenants. **Nothing is refused**: the
rule serves every shape a system has, and none is forced on a project.

| The context says | The owner of `drafts()` |
|---|---|
| nothing — a service keyed by its own ids | `tenant_id=,tenant_ids=,user_id=` |
| a tenant | `tenant_id=acme,tenant_ids=,user_id=` |
| a user | `tenant_id=,tenant_ids=,user_id=ana` |
| a user in a tenant | `tenant_id=acme,tenant_ids=,user_id=ana` |
| a user across several tenants | `tenant_id=,tenant_ids=acme\|globex,user_id=ana` |

A value that is `None`, empty or missing says nothing, as if the key were absent. Values are encoded,
so a `/`, a `,`, a `=`, a `:` or a `|` inside a tenant or a user is never read as a separator: a
tenant called `acme/user_id=bob` never reads the drafts of the user `bob` of `acme`. One draft meant
to be shared by everybody of a tenant editing a record is `by=[]`.

The desktop, the browser and the phone of one user in one tenant read and write the same draft of a
record: a draft belongs to no execution and no session, and outlives every browser that closed.

## Where it is kept

In the context's store — the one store the context has, and the one a project sets: a draft is an
*entry* of it, under its namespace, its owner and its subject:

```text
SINCPRO_ENTITY_DRAFT_CONTEXT : tenant_id=acme,tenant_ids=,user_id=ana : billing.Invoice%3A<uuid>
```

Set once — `bus.context_store(store)` for a bus, `use_context().root.set(ContextStore, store)` for
the process — or handed to the call, `drafts(store=...)`; with none set, the process's memory keeps
them, said once in the logs. Each store keeps an entry `kept_ttl` after its last keep, seven days
unless told: `KeyValueContexts(RedisKeyValue(redis), kept_ttl=timedelta(hours=1))`. A project's own
`ContextStore` keeps drafts when it implements its entries: `keep_entry`, `entry`, `discard_entry`,
`entries`.

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

`DraftConflict` is a `ClientError` — the caller read before someone else wrote — and declares
`FailureKind.CONFLICT`, so every wire answers it as a conflict — 409
over REST; an API that takes the version in `If-Match` answers the same failure as its 412.

A version never repeats for a key — not after a discard, not after an expiry — so a tab still
holding an old one can never bring a dropped draft back, nor overwrite a draft another tab
started after the old one expired.

## The TTL

Every draft has one, the store's `kept_ttl`, and every keep starts it again: an abandoned draft
disappears on its own and the store never grows without end. Days for an edit people come back
to, minutes or hours for a wizard.

## Activating a draft

Saving a draft is the project's Command. The draft never becomes the record by itself:

```python
@billing.feature(CommandActivateInvoiceDraft)
class ActivateInvoiceDraft(Feature):
    def execute(self, dto: CommandActivateInvoiceDraft) -> None:
        drafts = self.context.drafts()
        subject = f"invoice:{dto.invoice_id}"
        draft = drafts.read(subject)
        if draft is None:
            raise DraftConflict(subject, dto.version, 0)
        invoice = self.repository.get(Invoice, dto.invoice_id)
        refuse_stale(draft, invoice)                # somebody saved it since: StaleAggregate
        assign(invoice, draft.values)               # JSON read as each field's type
        self.repository.save(invoice)
        drafts.discard(subject)
```

`refuse_stale` compares the record's `version` with the draft's `origin_version` before the
draft's values touch the record. `assign` (`sincpro_framework.ddd`) reads each JSON value as its
field's type — `"1150.50"` becomes a `Decimal` — so the save sees what a typed edit would. Setting `version` back on a loaded record is not a check: the
mapping writes it as a new value and the save goes through.

## How the stores keep it

Both context stores keep their entries — drafts, pipeline runs — with the same machinery, written
once for each store and seen by nobody else.

`InMemoryContexts` keeps them in a dict behind a lock, for one process and for tests. It remembers a key's
last version for a TTL after its draft was last kept or discarded, then forgets it, so it does
not hold every key it ever saw; a chain started after that begins at the clock's milliseconds,
and always above any version the store handed out for any key — so a version never repeats,
not under a clock that stands still, not with a TTL shorter than the keeps it holds.

`KeyValueContexts` stands on any `KeyValueStore` — Redis, Valkey, Memcached — with nothing
store-specific. A draft is a chain of slots, `{prefix}:{key}:{version}`, each written once with
the store's atomic `add`: of two keeps naming the same version, exactly one writes the next
slot. A slot is never deleted — its TTL removes it — so its number is never handed out again; a
discard writes the next slot as spent. Two pointers, `{prefix}:{key}:head` and
`{prefix}:{key}:chain`, say where the chain was last seen; a reader takes the larger and probes
the slots after it, so a keep that won its slot and had not moved them yet is still read, and
both are written again. The store evicting one of them, or a process stopping between its slot
and them, loses nothing. Every key carries the TTL. Listing what an owner has a draft of —
`subjects()` — reads a list nothing ever rewrites: each new draft takes a position in it with the
store's atomic `add`, so drafts started at the same instant on any replica never lose one another,
and a hint with the TTL says where the list was last seen.

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
