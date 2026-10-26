# Edits: the life of a form, one command

A form creates a record or changes one over several requests, and is not always saved: the user
continues tomorrow, from another device, or drops it. `Edit` is that whole life in one command —
save the caller's draft, save the record, discard the draft — and `Get` / `Search` find a draft again after the
client lost its state. Nothing is stored until the form says save.

```python
from sincpro_framework.ddd import (
    Edit, EditAction, EntityEdits, Get, ResponseEdit, ResponsePaginatedQuery, ResponseRecord, Search,
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


@billing.feature([QueryGetInvoice, QueryListInvoices, CommandEditInvoice])
class InvoiceForms(EntityEdits[Invoice]):
    pass


billing.add_dependency("repository", repository)
billing.store_context_in(KeyValueContexts(RedisKeyValue(redis)))   # where drafts are stored
```

`EntityEdits` is an `EntityReads`: the one Feature answers the form's reads and its edits.

## The life of a form

| The client sends | What happens | The answer |
|---|---|---|
| `CommandEditInvoice(id=…, values={…})` | the record — the stored one, or a new one under `id` — with the form's values on it, its derived fields computed; saved as the caller's draft | the record, `draft.version`, each field's state, the advice |
| the same, `version=draft.version` | saved again over the version the client read | a new `draft.version` |
| `…, version=…, action=EditAction.SAVE` | the draft's values, then this edit's, on the stored record; saved; the draft dropped | the record as saved, `draft=None` |
| `…, action=EditAction.DISCARD` | the draft dropped | the record as stored, `draft=None` |
| `QueryGetInvoice(id=…, draft=True)` | the caller's draft when one is stored, the stored record otherwise | the record, `draft` saying which |
| `QueryListInvoices(criteria=…, drafts=True)` | the caller's drafts, by the same criteria | a page of them |

**The form sends the record whole**, as it holds it: every field it shows, as JSON — the lines
included, each line it got back named by its `id`, a new line without one. `changed` names what
moved, so only the derivations it reaches run; none runs them all.

**The id is the client's**: a new record is created under the `id` the client names — a UUID v7 it
mints — or one minted for it when it names none, answered back. The draft and the stored record
share it from the first draft save, so a record only drafted so far is found by its own id. A line
created in a draft retains the id it got through every draft save, and is stored under it.

**A save without a draft** is an edit too: `action=EditAction.SAVE` on a record nobody drafted
stores the values at once. It is refused with `StaleAggregate` when `origin_version` — the version
the client read — is no longer the stored one.

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

A field the record does not have, a value its type cannot read, a field the framework writes, a
line named by an id the record does not hold: the request is wrong, and `assign` refuses it with a
`ClientError` naming the field — 400 on every wire, its message told to the caller.

## Two tabs, and someone who saved meanwhile

Every edit names the draft version it read. A tab still on an old version loses with
`DraftConflict` (409) and reads again — nothing is overwritten in silence. A draft over a record
somebody saved since is `stale` on every answer, and its save is refused with `StaleAggregate`:
the stored record is never overwritten with what the draft started from.

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
generic one does — a draft save runs the domain over a copy inside `previewing()`, so nothing is
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
`drafts`.

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

## What it is built on

`Preview` answers "what would the record become" and stores nothing; an `Edit` with `SAVE_DRAFT` is
the same computation, stored. The draft holds the record's values as a form sends them (`form_values`):
nothing the framework writes, a nested record named by its id; reading it back puts them on the
stored record with `assign(record, values, adopt=True)`, so a line the draft created is built under
the id it already has. [Drafts](../core/drafts.md) says how a draft is stored and versioned.
