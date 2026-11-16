# Recipe: a form's whole life — `Edit` + `EntityEdits`, and `Delete`

Use it when a screen (or an agent through MCP) creates or changes one record over several
requests and does not always save: save the caller's draft, save the record, discard the draft, and find it
again after the client lost its state. Nothing is stored until it is saved. `Delete` removes the
record, with a dry run for the confirmation.

## 1. Declare the DTOs and one Feature

```python
from sincpro_framework.ddd import (
    Delete, Edit, EditAction, EntityEdits, Get, ResponseEdit, ResponseRecord, Search,
)

class ResponseEditInvoice(ResponseEdit):
    invoice: Invoice

class CommandEditInvoice(Edit[Invoice, ResponseEditInvoice]):
    pass

class CommandDeleteInvoice(Delete[Invoice]):  # answers nothing: 204 over REST, null in the contract
    pass

@billing.feature([QueryGetInvoice, QueryListInvoices, CommandEditInvoice, CommandDeleteInvoice])
class InvoiceForms(EntityEdits[Invoice]):     # an EntityReads too: the form's reads and edits
    pass

billing.store_context_in(KeyValueContexts(RedisKeyValue(redis)))   # drafts live where the context does
```

## 2. The client's calls

```python
CommandEditInvoice(id=new_id, values={...whole record...})                         # save the draft → draft.version
CommandEditInvoice(id=new_id, values={...}, changed=["discount"], version=v)        # save the draft again
CommandEditInvoice(id=new_id, version=v, action=EditAction.SAVE)                    # store, drop the draft
CommandEditInvoice(id=new_id, action=EditAction.DISCARD)                            # drop the draft
QueryGetInvoice(id=new_id, draft=True)                                              # after the browser closed
QueryListInvoices(criteria=Criteria(), drafts=True)                                 # what was I editing?
CommandDeleteInvoice(id=new_id, dry_run=True)                                       # Preview for the confirmation
CommandDeleteInvoice(id=new_id, origin_version=v)                                   # deleted; refused if it moved
```

Each answer is the record with its computed fields, `draft` (version, `stale`), `states` and
`warnings`. `DraftConflict` (409) when another tab saved its draft first; `StaleAggregate` when somebody saved
the record since the draft started — or deleted it.

`values` is the record as the form read it: its `id`, `version` and stamps come back with it and
are left out (`assignable`); the `version` is the one the edit started from. Only another record's
`id` is refused.

## 3. A command of the project's own

```python
class CommandAddInvoiceLine(Edit[Invoice, ResponseEditInvoice]):
    product: str
    qty: int

@billing.feature(CommandAddInvoiceLine)
class AddInvoiceLine(EntityEdits[Invoice]):
    def change(self, invoice: Invoice, dto: CommandAddInvoiceLine, /) -> None:
        invoice.add_line(dto.product, dto.qty)        # the domain's method: drafted, saved, discarded the same
```

## 4. A field a form must not write, and what a deletion leaves

```python
class BannerForms(EntityEdits[Banner]):
    def assignable(self, entity, key, values):        # the picture's key: only its upload sets it
        kept = super().assignable(entity, key, values)
        return {name: value for name, value in kept.items() if name != "picture"}

    def deleting(self, banner: Banner, dto, /) -> None:   # a Delete, and each record a Purge takes
        if banner.pinned:
            raise exceptions.DomainError("a pinned banner stays")
        self.repository.after_commit(lambda: self.files.delete(banner.picture))
```

The audit is not written here: an entity of a `ChangeTrackingMixin` base records `EntityDeleted`
by itself, stored with the removal. `deleting` never runs in a dry run.

## 5. A trash (soft delete) — only when the business asks for one

```python
@dataclass
class Banner(ArchivableMixin, SiteEntity): ...         # SiteEntity names delete/archive/restore_event

class CommandTrashBanner(Archive[Banner]): ...          # out of every reading, kept
class CommandRestoreBanner(Restore[Banner]): ...        # back
class CommandPurgeBanners(Purge[Banner]): ...           # archived longer than older_than: gone

# the project's own cron, nightly — the framework never schedules it
self.site(CommandPurgeBanners(older_than=timedelta(days=30)))
```

The usual case is a direct `Delete` with its audit; a trash is for a business that wants to bring
records back. The trash list is a `Search` naming `archived_at`.

## Mistakes to avoid

- **Sending only what changed in `values`.** Send the record whole, as the form holds it; name
  what moved in `changed`.
- **Saving the record by hand after a draft save.** `action=EditAction.SAVE` checks the draft version and
  staleness, runs `change` on the stored record and drops the draft.
- **A new record's id from the server only.** The client may mint it (UUID v7) and go on using it:
  the draft and the stored record share it.
- **Reading a draft implicitly.** `Get` answers the stored record unless the caller asks
  `draft=True`; business logic never reads someone's unsaved values.
- **Using it for a record others must see before it is saved** (an invoice awaiting approval):
  that is a stored record with a state.
- **Stripping `id`, `version` or the stamps on the client, or subclassing to drop them.** The edit
  leaves them out itself; the `version` sent back is what refuses a save over a record that moved.
- **A deletion Command and Feature written by hand, or its fact recorded by hand.** `Delete[T]`
  removes it, `deleting` refuses by the domain and waits for the commit, and `ChangeTrackingMixin`
  records `EntityDeleted` by itself.
- **A soft delete built by hand** (an `is_deleted` flag, a cron of your own that deletes): it is
  `ArchivableMixin` with `Archive` / `Restore` / `Purge`.
- **Answering something from a deletion.** It answers nothing; a `DELETE` route that answers a
  body is refused when the gateway starts.
