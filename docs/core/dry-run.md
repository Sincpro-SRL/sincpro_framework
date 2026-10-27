# Dry run: a Command that says what it would do, and does nothing

A write that a person — or an agent, a script, a screen — wants to see before it happens: the
quotation it would create, the mail it would send, the entries it would post. As in Kubernetes
(`dryRun`) or AWS (`DryRun`), the dry run is the same call: it goes through the same validation and
the same guards, and writes nothing. Here it is a field of the Command itself, so every caller says
it in the Command, and every wire carries it as it carries any field.

Every block on this page runs, in order, in `tests/docs/test_persistence_guide.py`.

## The Command, and its preview

```python
from sincpro_framework import DataTransferObject, DryRunnable, Feature, Preview, UseFramework


class CommandCreateQuotation(DryRunnable):          # dry_run: bool = False, inherited
    customer: str
    total: int


class ResponseQuotation(DataTransferObject):
    quotation_id: str


sales = UseFramework("sales-dry-run-guide", log_after_execution=False)
written: list[str] = []


@sales.feature(CommandCreateQuotation)
class CreateQuotation(Feature):
    def preview(self, dto: CommandCreateQuotation) -> Preview:          # validates, reads
        return Preview(text=f"Quotation for {dto.customer}, Bs {dto.total}")

    def execute(self, dto: CommandCreateQuotation) -> ResponseQuotation:  # writes
        written.append(dto.customer)
        return ResponseQuotation(quotation_id=f"Q-{len(written)}")


shown = sales(CommandCreateQuotation(customer="Juan", total=1150, dry_run=True), Preview)
assert shown.text == "Quotation for Juan, Bs 1150" and written == []

done = sales(CommandCreateQuotation(customer="Juan", total=1150), ResponseQuotation)
assert done.quotation_id == "Q-1" and written == ["Juan"]
```

- **`DryRunnable`** gives the Command `dry_run: bool = False`. Without it, the Command runs.
- **`preview`** answers a dry run with a `Preview` — `text` for a person, `data` for a screen of the
  project's own. Everything it must refuse — an id that does not exist, a total of nothing — it
  refuses here, as the run would.
- **`execute` never sees a dry run.** The bus runs it as any call — its context, its guards, its
  interceptors, its span — and answers it with `preview`. A handler with no `preview` gets the dry
  run in `execute`, and decides.

## Several use cases, one preview

An ApplicationService says what it would do by running dry what it would run — explicit, in each
Command:

```python
from sincpro_framework import ApplicationService


class CommandCreateContact(DryRunnable):
    name: str


class ResponseContact(DataTransferObject):
    contact_id: str


class CommandSell(DryRunnable):
    customer: str
    total: int


class ResponseSold(DataTransferObject):
    contact_id: str
    quotation_id: str


selling = sales.fresh()                           # a new generation of the bus, not built yet


@selling.feature(CommandCreateContact)
class CreateContact(Feature):
    def preview(self, dto: CommandCreateContact) -> Preview:
        return Preview(text=f"Contact {dto.name}")

    def execute(self, dto: CommandCreateContact) -> ResponseContact:
        written.append(f"contact {dto.name}")
        return ResponseContact(contact_id=f"C-{dto.name}")


@selling.app_service(CommandSell)
class Sell(ApplicationService):
    def preview(self, dto: CommandSell) -> Preview:
        contact = self.feature_bus(CommandCreateContact(name=dto.customer, dry_run=True), Preview)
        quotation = self.feature_bus(
            CommandCreateQuotation(customer=dto.customer, total=dto.total, dry_run=True), Preview
        )
        return Preview(text=f"{contact.text}; then {quotation.text}")

    def execute(self, dto: CommandSell) -> ResponseSold:
        contact = self.feature_bus(CommandCreateContact(name=dto.customer), ResponseContact)
        quotation = self.feature_bus(
            CommandCreateQuotation(customer=dto.customer, total=dto.total), ResponseQuotation
        )
        return ResponseSold(contact_id=contact.contact_id, quotation_id=quotation.quotation_id)


written.clear()
told = selling(CommandSell(customer="Juan", total=1150, dry_run=True), Preview)
assert told.text == "Contact Juan; then Quotation for Juan, Bs 1150" and written == []
```

`execute` may run its use cases dry first too — every one checked before the first write:
`self.feature_bus(cmd.model_copy(update={"dry_run": True}), Preview)`.

## Previewed by default

The default runs, as everywhere in the industry. A project whose writes must be previewed unless
the caller says otherwise — an agent's tools — says so once, on its own base:

```python
class WriteCommand(DryRunnable):
    dry_run: bool = True                          # previewed unless the caller says dry_run=False


class CommandSendMail(WriteCommand):
    to: str


mailing = UseFramework("mail-dry-run-guide", log_after_execution=False)
sent: list[str] = []


@mailing.feature(CommandSendMail)
class SendMail(Feature):
    def preview(self, dto: CommandSendMail) -> Preview:
        return Preview(text=f"A mail to {dto.to}")

    def execute(self, dto: CommandSendMail) -> None:
        sent.append(dto.to)


assert mailing(CommandSendMail(to="ana@acme.bo")) == Preview(text="A mail to ana@acme.bo")
mailing(CommandSendMail(to="ana@acme.bo", dry_run=False))
assert sent == ["ana@acme.bo"]
```

## Answered in `execute`, run once

A handler with no `preview` gets its dry run in `execute`, and answers it there — the gate a
project already has, with the flag in the Command. Its idempotency is declared once for every
write: a dry run is never claimed, so it is not stored and answers with the store down.

```python
from datetime import timedelta


class CommandUpdateDeal(WriteCommand):
    deal_id: int
    stage: str


class ResponseDeal(DataTransferObject):
    deal_id: int


crm = UseFramework("crm-dry-run-guide", log_after_execution=False)
crm.idempotency.declare_for(WriteCommand, expires_after=timedelta(minutes=2))
moved: list[str] = []


@crm.feature(CommandUpdateDeal)
class UpdateDeal(Feature):
    def execute(self, dto: CommandUpdateDeal) -> ResponseDeal | Preview:
        if dto.dry_run:
            return Preview(text=f"Deal {dto.deal_id} moves to {dto.stage}")
        moved.append(dto.stage)
        return ResponseDeal(deal_id=dto.deal_id)


with crm.context({"tenant_id": "acme", "user_id": "ana"}):
    assert crm(CommandUpdateDeal(deal_id=7, stage="won")) == Preview(
        text="Deal 7 moves to won"
    )
    crm(CommandUpdateDeal(deal_id=7, stage="won", dry_run=False))
    crm(CommandUpdateDeal(deal_id=7, stage="won", dry_run=False))     # a retry: replayed
assert moved == ["won"]
```

## From every caller

| Caller | How it asks |
|---|---|
| Code, a test, a script | `bus(CommandX(..., dry_run=True))` |
| A CLI | `--dry-run` filling the field |
| REST, RPC, a queue | `"dry_run": true` in the body, as any field |
| An MCP tool | the `dry_run` argument the tool already publishes from the Command |

A retried write runs once with `@bus.idempotency`, or `bus.idempotency.declare_for(WriteCommand,
...)` for every write at once: a dry run is never claimed — answered by `preview` or in `execute`,
it is not stored and never in `outcomes()`. A business metric (`@metrics.counts`, `sums`,
`measures`) counts nothing for it either: the duration is recorded with `sincpro.run="dry_run"`.
