# Edits: the life of a form, one command

A form creates a record or changes one over several requests, and is not always saved: the user
continues tomorrow, from another device, or drops it. `Edit` is that whole life in one command —
save the caller's draft, save the record, discard the draft — and `Get` / `Search` find a draft again after the
client lost its state. Nothing is stored until the form says save. `Delete` removes the record;
`Archive`, `Restore` and `Purge` give it a trash.

```python
from sincpro_framework.ddd import (
    Delete, Edit, EditAction, EntityEdits, Get, ResponseEdit, ResponsePaginatedQuery,
    ResponseRecord, Search,
)


class ResponseInvoice(ResponseRecord):
    invoice: Invoice


class ResponseEditInvoice(ResponseEdit):
    invoice: Invoice


class ResponseInvoices(ResponsePaginatedQuery):
    invoices: list[Invoice]


class QueryGetInvoice(Get[Invoice, ResponseInvoice]): ...
class QueryListInvoices(Search[Invoice, ResponseInvoices]): ...
class CommandEditInvoice(Edit[Invoice, ResponseEditInvoice]): ...
class CommandDeleteInvoice(Delete[Invoice]): ...


@billing.feature([QueryGetInvoice, QueryListInvoices, CommandEditInvoice, CommandDeleteInvoice])
class InvoiceForms(EntityEdits[Invoice]):
    pass


billing.add_dependency("repository", repository)
billing.store_context_in(KeyValueContexts(RedisKeyValue(redis)))   # where drafts are stored
```

`EntityEdits` is an `EntityReads`: the one Feature answers the form's reads and its edits.

## The life of a form

| The client sends | What happens | The answer |
|---|---|---|
| `CommandEditInvoice(id=…, values={…})` | the record — the stored one, or a new one under `id` — with the form's values on it, its computed fields up to date; saved as the caller's draft | the record, `draft.version`, each field's state, the warnings |
| the same, `version=draft.version` | saved again over the version the client read | a new `draft.version` |
| `…, version=…, action=EditAction.SAVE` | the draft's values, then this edit's, on the stored record; saved; the draft dropped | the record as saved, `draft=None` |
| `…, action=EditAction.DISCARD` | the draft dropped | the record as stored, `draft=None` |
| `QueryGetInvoice(id=…, draft=True)` | the caller's draft when one is stored, the stored record otherwise | the record, `draft` saying which |
| `QueryListInvoices(criteria=…, drafts=True)` | the caller's drafts, by the same criteria | a page of them |

**The form sends the record whole**, as it holds it: every field it shows, as JSON — the lines
included, each line it got back named by its `id`, a new line without one; a many-to-many as the
ids of the records it references (`{"labels": ["0192…"]}`), read before they are put on the
record and stored as the pairs of the table in between ([relations](relations.md#8-an-aggregate-is-saved-whole)).
`changed` names what moved, so only the computations it reaches run; none runs them all.

**What the framework writes comes back as the form read it.** The record's `id`, `version` and
stamps — a line's too — are the record's echo: left out of the values, never assigned and never
refused (`assignable`). The record's `version` among them is the version the edit started from, so
a form that sends the record it read gets the optimistic check without naming `origin_version`.
Only an `id` other than the record's is refused: it would put one record's values on another.

**The lines may come back as they were read.** A reading answers a to-many as a page —
`{"items": […], "count": {"value": 3, "exact": true}, "cursor": null}` — and a form may send that
same collection back: whole, it is written as its items. A page of it is refused with an
`InvalidValue` on the field — a cursor, a floor, or fewer items than counted —, because written
back it would remove every line it did not bring.

**The id is the client's**: a new record is created under the `id` the client names — a UUID v7 it
mints — or one minted for it when it names none, answered back. The draft and the stored record
share it from the first draft save, so a record only drafted so far is found by its own id. A line
created in a draft retains the id it got through every draft save, and is stored under it.

**A save without a draft** is an edit too: `action=EditAction.SAVE` on a record nobody drafted
stores the values at once. It is refused with `StaleAggregate` when `origin_version` — the version
the client read, or the record's own `version` sent back in the values — is no longer the stored
one.

## Whose draft

A draft is stored under its record's key, its entity and its bounded context —
`billing.Invoice:F-7` — so two contexts sharing one store, each with an `Invoice`, never read each
other's.

The caller's: the context says whose — the user, within the tenant (see [Drafts](../core/drafts.md)).
Two cashiers editing one invoice each have their own draft; a cashier of another shop never sees
either. Override `drafts()` on the Feature to key them by something else:

```python
class InvoiceForms(EntityEdits[Invoice]):
    def drafts(self) -> Drafts:
        return use_context().drafts(by=["session_id"])
```

## What the form sent wrong

A field the record does not have, a value its type cannot read, another record's `id`, a line
named by an id the record does not hold: the request is wrong, and it is refused with
`InvalidValue` — a `ClientError` — naming the field, 422 on every wire, its message told to the
caller. It carries where the value is, a line's cell included — the line by its identity —, so the
`Problem` points at it and a screen marks that cell:

```json
{"kind": "invalid", "reason": "INVALID_VALUE", "status": 422,
 "errors": [{"pointer": "#/values/lines/0192…/qty", "detail": "InvoiceLine.qty cannot take 'dos': …"}]}
```

## Two tabs, and someone who saved meanwhile

Every edit names the draft version it read. A tab still on an old version loses with
`DraftConflict` (409) and reads again — nothing is overwritten in silence. A draft over a record
somebody saved since is `stale` on every answer, and its save is refused with `StaleAggregate`:
the stored record is never overwritten with what the draft started from. A draft over a record
deleted since is stale too: saving it never brings the record back.

## When the client lost its state

The browser closed, the agent's conversation ended: the draft is on the server, stored by whose it
is. The client asks again —

```python
QueryGetInvoice(id="0192…", draft=True)              # the invoice where it was left, with its version
QueryListInvoices(criteria=Criteria(), drafts=True)  # what was I editing? — the most recent first
```

— and goes on from the version answered. Only what was typed after the last draft save is lost.

## A command of the project's own

A command that changes the record with the domain's methods is an `Edit` with fields of its own,
answered by an `EntityEdits` that overrides `change`. It saves drafts, saves, discards and answers as the
generic one does — a draft save runs the domain over a copy inside `read_only()`, so nothing is
written; a save runs it on the stored record, and the events it records are saved with it.

```python
class CommandAddInvoiceLine(Edit[Invoice, ResponseEditInvoice]):
    product: str
    qty: int
    price: Decimal


@billing.feature(CommandAddInvoiceLine)
class AddInvoiceLine(EntityEdits[Invoice]):
    def change(self, invoice: Invoice, dto: CommandAddInvoiceLine, /) -> None:
        invoice.add_line(dto.product, dto.qty, dto.price)
```

Each step is a method to override and call `super()` in: `save_draft`, `save`, `discard`, `change`,
`assignable`, `drafts` — and `delete`, `archive`, `restore`, `purge` with their hooks `deleting`,
`archiving`, `restoring`.

## What a form must not write

A field the form shows but only a command of its own writes — a picture's key, set by its upload —
is left out where the form's values are read, `assignable`. The preview and every save leave it out
alike, and the stored value stays:

```python
class BannerForms(EntityEdits[Banner]):
    def assignable(self, entity: type, key: str | None, values: dict) -> dict:
        return {
            name: value
            for name, value in super().assignable(entity, key, values).items()
            if name != "picture"
        }
```

Shown read-only, it is a hint in `presentation` (`readonly`): the form draws it so, nothing refuses it.

## A transition: confirm, post, approve

An ERP's confirm step — post an invoice, confirm an order, approve a request — changes the state
and often takes a number. It is an `Edit` whose action is `SAVE`: the caller's draft, if any, then
the domain's own method, stored in one write. The number is taken only then, never by a draft.

```python
class CommandPostInvoice(Edit[Invoice, ResponseEditInvoice]):
    action: EditAction = EditAction.SAVE


@billing.feature(CommandPostInvoice)
class PostInvoice(EntityEdits[Invoice]):
    def change(self, invoice: Invoice, dto: CommandPostInvoice, /) -> None:
        super().change(invoice, dto)                            # what the form still held
        invoice.post(self.numbering.take("invoices")[0])        # the domain decides; refused twice
```

Who may post is the bus's access control on `CommandPostInvoice`, as for any command.

## Deleting

`Delete` removes a record by its key, and answers nothing — a `204` over REST, `null` in the
contract, the `delete` role beside the entity. It is a `DryRunnable`: `dry_run=True` finds and checks
the record as the deletion would, deletes nothing, and answers a `Preview` — what a confirmation
shows, and over REST a `200` with it.

| The client sends | What happens | The answer |
|---|---|---|
| `CommandDeleteInvoice(id=…)` | the stored record removed — the parts it owns with it — and the caller's draft of it dropped | nothing |
| `…, origin_version=N` | refused with `StaleAggregate` when the record moved past `N` | — |
| `…, dry_run=True` | nothing deleted | `Preview(text="Would delete Invoice … «…»", data={"id", "version"})` |
| a key with no record | `AggregateNotFound` | — |

**Its history keeps it.** An entity with `ChangeTrackingMixin` records `EntityDeleted` when the
store removes it — whichever way: this `Delete`, a `repository.remove`, a `Purge` — stored with
the removal in its context's event table, so `DomainEvents` still answers it after the row is
gone ([the context's facts](guide.md#10-change-tracking-and-the-contexts-event-table)).

`deleting(record, dto)` is what the deletion does with the record before it goes: nothing by
default. Override it to refuse when the domain says the record cannot go, to record a fact of the
domain's own, and to wait for the commit for what lives outside the store:

```python
@billing.feature(CommandDeleteBanner)
class BannerDeletion(EntityEdits[Banner]):
    def deleting(self, banner: Banner, dto: CommandDeleteBanner, /) -> None:
        if banner.pinned:
            raise exceptions.DomainError("a pinned banner stays")
        self.repository.after_commit(lambda: self.files.delete(banner.picture))
```

The deletion runs in one unit of work when the store transacts, so what `deleting` waits for runs
once it committed, and none of it when it is undone. A dry run never runs `deleting`.

## A trash: archive, restore, purge

A record that goes to a trash before it is deleted is `ArchivableMixin`. Three more built-ins,
each a `DryRunnable` that answers nothing, give it the trash — and only when the project declares
them:

```python
@dataclass
class Banner(ArchivableMixin, SiteEntity):      # SiteEntity: the context's ChangeTrackingMixin base
    title: str = ""

class CommandTrashBanner(Archive[Banner]): ...      # out of every reading, kept
class CommandRestoreBanner(Restore[Banner]): ...    # back into every reading
class CommandPurgeBanners(Purge[Banner]): ...       # the long archived, deleted for good
```

| The client sends | What happens | Its fact |
|---|---|---|
| `CommandTrashBanner(id=…)` | `archived_at` stamped: every reading leaves it out, nothing is lost; the caller's draft dropped | `EntityArchived` |
| `CommandRestoreBanner(id=…)` | `archived_at` cleared: read again | `EntityRestored` |
| `CommandPurgeBanners(older_than=timedelta(days=30))` | every record archived more than 30 days ago removed, `batch_size` (500) per unit of work, `deleting` run for each | `EntityDeleted` each |
| any of them, `dry_run=True` | nothing written, no hook run | `Preview` — a `Purge`'s says how many (`data={"count": …}`) |

`archiving(record, dto)` and `restoring(record, dto)` are the hooks of the other two, as
`deleting` is of a deletion — a purge runs `deleting` too, so a picture is deleted with its banner
whichever way the banner goes. `purgeable(entity, dto)` is what a purge reads; override it to keep
some longer. The trash itself is a `Search` that names `archived_at`, which every other reading
leaves out:

```python
QueryListBanners(criteria=Criteria(
    selection=Condition(field="archived_at", operator=Operator.IS_NULL, value=False)
))
```

**The framework never purges on its own.** A purge runs when a cron of the project runs it:

```python
@cron_site.cron("0 3 * * *", timezone="America/La_Paz", overlap=Overlap.SKIP)
class EmptyTheTrash(Cron):
    def run(self, tick: Tick) -> None:
        self.site(CommandPurgeBanners(older_than=timedelta(days=30)))
```

A purge that meets a record the domain refuses stops there; the batches before it stay deleted,
and the next run starts again from what is left.

## Which one, when

| What the business wants | What the project declares |
|---|---|
| **Delete, and an audit of it** — the usual case | `Delete[T]` on an entity of a `ChangeTrackingMixin` base that names its `delete_event`: the record goes, the fact stays in the context's history |
| Delete, and clean up what lives outside the store (a file, a cache entry) | the same, with `deleting` waiting for the commit (`after_commit`) |
| Delete only what the domain allows (a posted invoice stays) | `deleting` raising a `DomainError` |
| **A trash**: put away, bring back within a time, then gone for good | `ArchivableMixin`, `Archive[T]` + `Restore[T]`, and a `Purge[T]` a cron of the project runs |
| Put away for ever — what invoices still point at | `ArchivableMixin` and `Archive[T]`, no `Purge` |
| React elsewhere — notify, reindex, purge something else | a subscriber of `EntityDeleted` / `EntityArchived` / `EntityRestored` (or the context's own subclasses), published on commit like any fact |
| Confirm before any of them | the same command with `dry_run=True` first: its `Preview`, then the command with the `version` the preview answered as `origin_version` |

## What it is built on

`PreviewEntity` answers "what would the record become" and stores nothing; an `Edit` with `SAVE_DRAFT` is
the same computation, stored. The draft holds the record's values as a form sends them (`form_values`):
nothing the framework writes, a nested record named by its id; reading it back puts them on the
stored record with `apply_values(record, values)`, so a line the draft created is built under
the id it already has — the one the form minted for it. [Drafts](../core/drafts.md) says how a draft is stored and versioned.
