# Approvals: a use case that runs only once a person approved it

An agent writing in a customer's system, a payment, an email sent for someone: the use case must
not run until a person has read what it will do and said yes, and then it must run exactly what
they read, once. An approval is that, declared on the use case: the first call answers what it
would do and stores it; `Approve` runs the input stored, never one sent again.

Every block on this page runs, in order, in `tests/docs/test_persistence_guide.py`.

## Declaring one

```python
from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.approvals import (
    Approval,
    ApprovalRequired,
    Approvals,
    Approve,
    FindApprovals,
    GetApproval,
    Reject,
)
from sincpro_framework.context import InMemoryContexts


class CommandCreateQuotation(DataTransferObject):
    customer: str
    total: int


class ResponseQuotation(DataTransferObject):
    quotation_id: str


sales = UseFramework("sales-approvals-guide", log_after_execution=False)
sales.store_context_in(InMemoryContexts())        # RedisKeyValue on replicas: every one decides
written: list[str] = []


@sales.feature(CommandCreateQuotation)
class CreateQuotation(Feature):
    approval = Approval()                         # 15 minutes to decide, by default

    def preview(self, dto: CommandCreateQuotation) -> str:   # read, never written
        return f"Quotation for {dto.customer}, Bs {dto.total}"

    def execute(self, dto: CommandCreateQuotation) -> ResponseQuotation:
        written.append(dto.customer)
        return ResponseQuotation(quotation_id=f"Q-{len(written)}")


@sales.feature([Approve, Reject, GetApproval, FindApprovals])
class SalesApprovals(Approvals): ...             # decides and reads every approval of the bus


with sales.context({"tenant_id": "acme", "user_id": "ana"}):
    asked = sales(CommandCreateQuotation(customer="Juan", total=1150))
    assert isinstance(asked, ApprovalRequired) and written == []
    assert asked.preview.text == "Quotation for Juan, Bs 1150"

    done = sales(Approve(approval_id=asked.approval_id), ResponseQuotation)
    assert done.quotation_id == "Q-1" and written == ["Juan"]

    again = sales(Approve(approval_id=asked.approval_id), ResponseQuotation)
    assert again == done and written == ["Juan"]    # answered again, run once
```

- **`preview` says what would be done**, with no effects: a `str`, or a `Preview(text, data)` with
  values for a screen of the project's own. Everything it must refuse — an id that does not exist,
  a total of nothing — it refuses here, before anything is stored.
- **The first call answers `ApprovalRequired`** — `approval_id`, `preview`, `expires_at`,
  `status="approval_required"` — and stores the input as it was asked. It is an answer, not an
  error: every wire carries it as a result, and no error rate counts it.
- **`Approve(approval_id)` runs the stored input**, never one sent again: nothing a caller sends
  with the decision can change what runs.
- **Once.** The approval is taken with the version it was read at, and the run is keyed by it in
  `bus.idempotency`: of two approvals at once, on two replicas, one runs and both get its answer;
  any approval after it answers the same. `bus.idempotency.outcomes()` around the call says
  `REPLAYED`, so a client is never told something was done twice.
- **The bus attaches the gate.** Nothing is wired by hand: a Feature or an ApplicationService
  whose class declares `approval = Approval()` asks for one, and the guards and interceptors of the
  project run around its preview as around its run. One without a `preview`, or whose `execute`
  declares no response, is a `ProgrammingError` when the bus is built.

## Deciding, and reading what is pending

```python
from sincpro_framework.approvals import (
    ApprovalClosed,
    ApprovalRecord,
    ApprovalState,
    ResponseApprovals,
)

with sales.context({"tenant_id": "acme", "user_id": "ana"}):
    other = sales(CommandCreateQuotation(customer="Rosa", total=80))
    pending = sales(FindApprovals(), ResponseApprovals)
    assert [one.approval_id for one in pending.approvals] == [other.approval_id]

    rejected = sales(Reject(approval_id=other.approval_id, reason="wrong price"), ApprovalRecord)
    assert rejected.state is ApprovalState.REJECTED and rejected.reason == "wrong price"
    try:
        sales(Approve(approval_id=other.approval_id))
    except ApprovalClosed as closed:
        assert closed.state is ApprovalState.REJECTED
```

| It is | Approving it | Rejecting it |
|---|---|---|
| pending | runs its input, once | closes it, with the reason |
| approved | answers what it ran again — past `replayed_for` (a day), `ApprovalClosed`: it never runs twice | `ApprovalClosed`: it ran |
| rejected | `ApprovalClosed` | the same, again |
| expired — nobody decided before `expires_at` | `ApprovalClosed` | stays expired |
| someone else's | `ApprovalNotFound` | `ApprovalNotFound` |

A run that fails — the customer's system did not answer — leaves the approval pending, to approve
again or reject. `GetApproval` reads one; `FindApprovals(state=...)` the caller's, the most
recently asked first, the pending ones by default.

## Whose it is, and where it is stored

An approval is the caller's: the tenant's and the user's (`Owner.BY_TENANT_AND_USER`), so a person
decides what they asked and nobody else of the tenant finds it. `owned_by = Owner.BY_TENANT` on the
`Approvals` class lets anyone of the tenant decide; whatever else decides — a permission, someone
who is not the one who asked — is the guard of the project around `Approve`.

It is an entry of the context's store, beside the drafts and the pipeline runs:

```text
SINCPRO_APPROVAL_CONTEXT : tenant_id=acme,tenant_ids=,user_id=ana : 9fK…
└─ namespace               └─ owner                                  └─ the approval
```

The id is `secrets.token_urlsafe`: random, never guessed. While the store does not answer, an
approval can be neither asked nor decided (`StoreUnavailable`): a use case that needs one does not
run, and never pretends to have stored what a person will approve.

## One approval for several use cases

An ApplicationService that declares an approval asks one for everything it runs. Its `preview`
calls the use cases it will run: inside a preview, each answers its own preview and runs nothing.
Once approved, the use cases it runs never ask again:

```python
from sincpro_framework import ApplicationService


class CommandCreateContact(DataTransferObject):
    name: str


class ResponseContact(DataTransferObject):
    contact_id: str


class CommandSell(DataTransferObject):
    customer: str
    total: int


class ResponseSold(DataTransferObject):
    contact_id: str
    quotation_id: str


selling = sales.fresh()                           # a new generation of the bus, not built yet


@selling.feature(CommandCreateContact)
class CreateContact(Feature):
    approval = Approval()

    def preview(self, dto: CommandCreateContact) -> str:
        return f"Contact {dto.name}"

    def execute(self, dto: CommandCreateContact) -> ResponseContact:
        written.append(f"contact {dto.name}")
        return ResponseContact(contact_id=f"C-{dto.name}")


@selling.app_service(CommandSell)
class Sell(ApplicationService):
    approval = Approval()

    def preview(self, dto: CommandSell) -> str:
        contact = self.feature_bus(CommandCreateContact(name=dto.customer), ApprovalRequired)
        quotation = self.feature_bus(
            CommandCreateQuotation(customer=dto.customer, total=dto.total), ApprovalRequired
        )
        return f"{contact.preview.text}; then {quotation.preview.text}"

    def execute(self, dto: CommandSell) -> ResponseSold:
        contact = self.feature_bus(CommandCreateContact(name=dto.customer), ResponseContact)
        quotation = self.feature_bus(
            CommandCreateQuotation(customer=dto.customer, total=dto.total), ResponseQuotation
        )
        return ResponseSold(contact_id=contact.contact_id, quotation_id=quotation.quotation_id)


written.clear()
with selling.context({"tenant_id": "acme", "user_id": "ana"}):
    asked = selling(CommandSell(customer="Juan", total=1150))
    assert asked.preview.text == "Contact Juan; then Quotation for Juan, Bs 1150"

    sold = selling(Approve(approval_id=asked.approval_id), ResponseSold)
    assert written == ["contact Juan", "Juan"] and sold.contact_id == "C-Juan"
```

What any of them refuses in its preview refuses the whole: nothing is stored, nothing written.
Everything the ApplicationService will write is checked there, before the first write — that is
its `preview`'s to do.

## On the wires

`ApprovalRequired` is a result everywhere: an MCP tool answers
`{"status": "approval_required", "approval_id": …, "preview": {"text": …}, "expires_at": …}` for the
model to show a person, and `Approve` / `Reject` are tools of their own — the model names the
approval, never sends the arguments again. On REST they are routes like any Command's. The span of
each use case says `approval.state` — `pending`, `previewed`, `approved`, `replayed`, `rejected`,
`expired` — and never the preview.

A sequence that waits for a decision between its steps is a pipeline's `Await` — see
[pipelines](pipelines.md#waiting-for-a-decision).
