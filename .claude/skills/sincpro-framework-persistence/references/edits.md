# Recipe: a form's whole life — `Edit` + `EntityEdits`

Use it when a screen (or an agent through MCP) creates or changes one record over several
requests and does not always save: keep the caller's draft, save it, discard it, and find it
again after the client lost its state. Nothing is stored until it is saved.

## 1. Declare the DTOs and one Feature

```python
from sincpro_framework.ddd import Edit, EditAction, EntityEdits, Get, ResponseEdit, ResponseRecord, Search

class ResponseEditInvoice(ResponseEdit):
    invoice: Invoice

class CommandEditInvoice(Edit[Invoice, ResponseEditInvoice]):
    pass

@billing.feature([QueryGetInvoice, QueryListInvoices, CommandEditInvoice])
class InvoiceForms(EntityEdits[Invoice]):     # an EntityReads too: the form's reads and edits
    pass

billing.context_store(KeyValueContexts(RedisKeyValue(redis)))   # drafts live where the context does
```

## 2. The client's calls

```python
CommandEditInvoice(id=new_id, values={...whole record...})                         # keep → draft.version
CommandEditInvoice(id=new_id, values={...}, changed=["discount"], version=v)        # keep again
CommandEditInvoice(id=new_id, version=v, action=EditAction.SAVE)                    # store, drop the draft
CommandEditInvoice(id=new_id, action=EditAction.DISCARD)                            # drop the draft
QueryGetInvoice(id=new_id, draft=True)                                              # after the browser closed
QueryListInvoices(criteria=Criteria(), drafts=True)                                 # what was I editing?
```

Each answer is the record with its derived fields, `draft` (version, `stale`), `fields` and
`advice`. `DraftConflict` (409) when another tab kept first; `StaleAggregate` when somebody saved
the record since the draft started.

## 3. A command of the project's own

```python
class CommandAddInvoiceLine(Edit[Invoice, ResponseEditInvoice]):
    product: str
    qty: int

@billing.feature(CommandAddInvoiceLine)
class AddInvoiceLine(EntityEdits[Invoice]):
    def change(self, invoice: Invoice, dto: CommandAddInvoiceLine, /) -> None:
        invoice.add_line(dto.product, dto.qty)        # the domain's method: kept, saved, discarded the same
```

## Mistakes to avoid

- **Sending only what changed in `values`.** Send the record whole, as the form holds it; name
  what moved in `changed`.
- **Saving the record by hand after a keep.** `action=EditAction.SAVE` checks the draft version and
  staleness, runs `change` on the stored record and drops the draft.
- **A new record's id from the server only.** The client may mint it (UUID v7) and keep using it:
  the draft and the stored record share it.
- **Reading a draft implicitly.** `Get` answers the stored record unless the caller asks
  `draft=True`; business logic never reads someone's unsaved values.
- **Using it for a record others must see before it is saved** (an invoice awaiting approval):
  that is a stored record with a state.
