# Span attributes: what a use case says about itself

The bus opens a span for every use case, named `context/Command`, and fills in what the framework
knows: the context, the use case, the layer, the outcome. What only the use case knows — the NIT
that issued an invoice, the branch and point of sale, the code SIAT answered, the merchant, the
QR, the bank's transaction id — it puts on that same span with `traces`: **declared** on the
class with `of()`, as a metric is, or **by hand** inside `execute` for a value known only midway.

The design and the why are [PRD 03 §3.1](../prd/PRD_03_observability-tracing.md#31-what-a-use-case-says-about-itself).

Every block on this page runs, in order, in `tests/docs/test_persistence_guide.py`.

## Why on the trace, and not on a metric

A NIT, a merchant or a transaction id vary without bound. As a metric label each distinct value is
one more series in Prometheus for as long as it lives — which is why a label that is not an
`Enum`, a `Literal` or a `bool` is warned about ([metrics](metrics.md#labels-the-projects-choice-with-one-warning)). On a span they cost
nothing extra, and they answer what the metrics cannot:

| Question | Where |
|---|---|
| the rejected invoices of NIT X, last hour | Tempo: `{span.sincpro.use_case="CmdSendDocumentToSiat" && span.siat.nit="1020304050" && span.sincpro.outcome!="ok"}` |
| invoices sent per company, without one series per company | TraceQL metrics: `{span.sincpro.use_case="CmdSendDocumentToSiat"} \| count_over_time() by (span.siat.nit)` |
| the exact send behind a log line or an alert | the line's `trace_id` opens the span; the span carries the NIT and the reception code |
| auditing one rejection | the span of that run: who sent it, from which branch, what SIAT answered |

It is **not a metric**: nothing is counted or summed, and it does not replace `@metrics.counts` /
`sums` / `measures` — what a dashboard must plot for months is a metric, bounded; what a person
must find is a span attribute. A TraceQL metric over span attributes is computed from the traces
that were sampled and kept, so it is for looking, not for billing.

## The context

```python
from decimal import Decimal
from enum import StrEnum

from sincpro_framework import ApplicationService, DataTransferObject, Feature, UseFramework
from sincpro_framework.observability import of, traces


class Environment(StrEnum):
    PRODUCTION = "production"
    TEST = "test"


class CommandSendInvoice(DataTransferObject):
    nit: int | str
    branch_office: int
    point_of_sale: int
    environment: Environment
    xml: bytes = b""


class ResponseSendInvoice(DataTransferObject):
    reception_code: str
    total: Decimal


siat = UseFramework("siat", log_after_execution=False)
```

## Declared: on the class, read off the Command and the Response

```python
@siat.feature(CommandSendInvoice)
@traces.attributes(
    of(CommandSendInvoice).nit,  # siat.nit
    of(CommandSendInvoice).environment,  # siat.environment
    of(ResponseSendInvoice).reception_code,  # siat.reception_code
    namespace="siat",
    branch=of(CommandSendInvoice).branch_office,  # siat.branch — named, not derived
    pos=of(CommandSendInvoice).point_of_sale,  # siat.pos
)
class SendInvoice(Feature):
    def execute(self, dto: CommandSendInvoice) -> ResponseSendInvoice:
        return ResponseSendInvoice(reception_code="R-77", total=Decimal("15.50"))


answer = siat(
    CommandSendInvoice(
        nit=1020304050, branch_office=0, point_of_sale=1, environment=Environment.TEST
    ),
    ResponseSendInvoice,
)
assert answer is not None and answer.reception_code == "R-77"
```

- **The key** is `{namespace}.{path}` for a reference (`of(X).a.b` → `siat.a.b`) and
  `{namespace}.{name}` for a named one. `namespace` is required.
- **When**: what starts at the **Command** is set before `execute` runs — a run that fails still
  carries it, and the failed runs are the ones someone will look for. What starts at the
  **Response** is set after a successful run.
- **Where**: on the span the bus opened for this use case, never on a child. An
  ApplicationService's attributes stay on its span; each Feature it runs keeps its own.
- **Refused at import**, as a metric is: a field the DTO lacks, a path into a DTO that is neither
  the use case's Command nor its Response, a value a span cannot hold (a DTO, a mapping, `bytes`),
  a key that breaks the rules below, one declared twice.

## By hand, inside `execute`: known only midway

```python
class CommandCreateQR(DataTransferObject):
    merchant_id: str
    amount: Decimal


class ResponseCreateQR(DataTransferObject):
    qr_id: int


payments = UseFramework("payments-qr", log_after_execution=False)


@payments.feature(CommandCreateQR)
@traces.attributes(namespace="payment", merchant_id=of(CommandCreateQR).merchant_id)
class CreateQR(Feature):
    def execute(self, dto: CommandCreateQR) -> ResponseCreateQR:
        bank_reference = "BNB-000981"  # what the bank answered
        traces.annotate({"payment.bank_reference": bank_reference, "payment.retried": False})
        return ResponseCreateQR(qr_id=42)


assert payments(CommandCreateQR(merchant_id="M-1", amount=Decimal(10)), ResponseCreateQR)
```

`traces.annotate` writes on the span of the use case running **now** — the one the bus opened for
it — even while an adapter's own span (an HTTP client, a SQL statement) is the active one. It works
in a Feature, an ApplicationService and an interceptor, and across `thread_context()` and the
async bus, which carry the context.

## In an ApplicationService

```python
class CommandIssue(DataTransferObject):
    nit: str


class ResponseIssue(DataTransferObject):
    cuf: str


issuing = UseFramework("issuing", log_after_execution=False)


@issuing.feature(CommandSendInvoice)
class Send(Feature):
    def execute(self, dto: CommandSendInvoice) -> ResponseSendInvoice:
        traces.annotate({"siat.attempt": 1})  # on issuing/CommandSendInvoice
        return ResponseSendInvoice(reception_code="R-1", total=Decimal(1))


@issuing.app_service(CommandIssue)
@traces.attributes(of(CommandIssue).nit, of(ResponseIssue).cuf, namespace="siat")
class Issue(ApplicationService):
    def execute(self, dto: CommandIssue) -> ResponseIssue:
        sent = self.feature_bus.execute(
            CommandSendInvoice(
                nit=dto.nit, branch_office=0, point_of_sale=0, environment=Environment.TEST
            ),
            ResponseSendInvoice,
        )
        traces.annotate({"siat.reception_code": sent.reception_code})  # on issuing/CommandIssue
        return ResponseIssue(cuf="ABC")


assert issuing(CommandIssue(nit="1020304050"), ResponseIssue)
```

`siat.nit` and `siat.cuf` land on `issuing/CommandIssue`, `siat.attempt` on
`issuing/CommandSendInvoice`: after `self.feature_bus.execute(...)` returns, the use case running
is the ApplicationService again.

## The rules

**Keys** are any text. `siat.nit`, `payment.merchant_id`, `siat.point_of_sale` — the domain
first reads best — but nothing is refused for its name: what a service shows is its decision,
and `hide_in_logs` is the tool to keep a key off every signal. A key the framework also writes
(`sincpro.outcome`) takes the project's value.

```python
class CommandLogin(DataTransferObject):
    access_token: str


assert callable(traces.attributes(of(CommandLogin).access_token, namespace="auth"))
```

A declaration is refused only for what could never work: a field that does not exist, a value a
span cannot hold, a key declared twice, nothing declared.

**The execution context goes on the span by itself.** Every key of `bus.context(...)` — and what
an interceptor, a hook or `execute` wrote to it — is an attribute of the use case's span when it
closes, with `tenant` and `user_id` (from the authenticated identity when the context has none).
See [correlation](correlation.md). A reference into the context type reads it too:
`@traces.attributes(of(BillingContext)["user_id"], namespace="billing")`.

**Values** are what OpenTelemetry holds: `str`, `bool`, `int`, `float`, and homogeneous sequences
of them. The framework also takes an `Enum` (as its value), a `Decimal` (as a float), a `UUID` (as
text) and a `date`/`datetime` (ISO 8601); a mixed sequence travels as text. `None` stays off the
span. Keep values short: `OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT` truncates what is longer.

## Nothing here can fail a use case

| Situation | What happens |
|---|---|
| a declaration that could never work (no such field, a value a span cannot hold) | refused at import (`ContractViolation`) — before any run |
| `traces.annotate` with a value a span cannot hold | that attribute is dropped and logged once; the rest are set; the use case runs |
| no `[opentelemetry]`, `OTEL_SDK_DISABLED`, no endpoint and no host provider | both doors do nothing; a declared run pays one check |
| `traces.annotate` outside a use case (a script, the transport before the bus) | nothing |
| a trace that was sampled out | the span does not record; nothing to see, nothing raised |

```python
traces.annotate({"payment.order": "O-1", "payment.payload": {"not": "a span value"}})
```

## Reference

| | |
|---|---|
| `@traces.attributes(*of(X).field, namespace=, **name=of(X).field)` | declared on a use case: the Command's before it runs, the Response's after a success |
| `traces.annotate({key: value})` | by hand, on the span of the use case running now |
| `of(Dto).field`, `of(ContextType)["key"]` | the field reference `metrics` uses — also exported by `sincpro_framework.observability` |
| `SpanValue` | what a value may be |
