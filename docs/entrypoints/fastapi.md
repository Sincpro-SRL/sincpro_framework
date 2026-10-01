# REST on FastAPI

`FastApiGateway` publishes the use cases of N buses as REST on FastAPI (`[fastapi]` extra): one
`APIRouter` per bounded context, one OpenAPI document for the routes it generates **and** the ones
a project writes by hand, every failure as RFC 9457 problem details. It replaces the Starlette
emitter of [`rest.md`](rest.md), which stays as the minimal host and gets no new features.

Every block on this page runs, in order, in `tests/docs/test_persistence_guide.py`.

## Where each piece lives

The buses, Features and ApplicationServices already exist. Publishing them adds **decorators to
those classes** and **one file that builds the app**. Nothing else:

```text
my_service/
  domains/billing/
    infrastructure/framework.py      billing = UseFramework("billing")       (already there)
    services/issue_invoice.py        @billing.feature(CommandIssueInvoice)   (already there)
                                     @rest.post("/invoices")                 ← add the route here
                                     class IssueInvoice(Feature): ...
  entrypoints/http/app.py            api = FastApiGateway({"billing": billing})
                                     app = api.app()                         ← the only new file
```

- The `@rest...` decorator goes on the **existing** handler in `services/`. It imports no web
  library, so the domain still knows nothing of HTTP.
- `entrypoints/` builds the gateway and, when a route has to be written by hand, holds that
  route. It never registers a Feature, an ApplicationService or a bus of its own: a use case
  that exists only to be called over HTTP is a second bus surface that the other wires (MCP,
  RPC, tests) do not see.
- When the body the client sends is not the Command (a file as base64, a field renamed), the
  route is written by hand and calls the bus with `bus_call`.
  [A DTO that cannot travel as JSON](#a-dto-that-cannot-travel-as-json) shows it.

## Two profiles

| Profile | What is published | Paths |
|---|---|---|
| `resource` (the default) | only the use cases with a `@rest...` binding (PRD_14 `DECLARED`) | what each binding declares, under the group's prefix |
| `rpc` (`profile="rpc"`) | every use case (`CATALOG`), each one logged | `POST /{group}/{kebab(Dto)}`; a `Query` on `GET`, and on `POST` for a criteria too long for a URL. This is RPC over HTTP, not REST |

What a binding leaves out is derived: `GET` for a `Query`, `POST` for any other Command, `204` for a
use case whose `execute` answers `None`. The `operationId` is the DTO's name. When two contexts
publish the same name, it becomes `{alias}_{Dto}`. A path field (`{invoice_id}`) must be a field of
the Command, and a `location` field (`{number}`) a field of the response. The gateway refuses the
surface otherwise, and it also refuses two operations on one method and path, or two with one
`operationId`.

```python
import json

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.auth import AccessControl, Identity, Permission, StaticProvider
from sincpro_framework.ddd.exceptions import DomainError
from sincpro_framework.ddd.query import Query
from sincpro_framework.entrypoints.exposure import rest
from sincpro_framework.entrypoints.fastapi import (
    BusCall,
    FastApiGateway,
    bus_call,
    install_problem_handlers,
    operation_extra,
    problem_responses,
)
from sincpro_framework.transport.failures import FailureKind


class BillingPermission(Permission):
    ISSUE = "billing.invoice.issue"


class CommandIssueInvoice(DataTransferObject):
    total: int


class Issued(DataTransferObject):
    number: str


class QueryInvoice(Query):
    invoice_id: str


class Invoice(DataTransferObject):
    invoice_id: str
    status: str = "issued"


class CommandCancelInvoice(DataTransferObject):
    invoice_id: str
    why: str


class CommandRemoveInvoice(DataTransferObject):
    invoice_id: str


class InvoiceNotFound(DomainError):
    failure_kind = FailureKind.NOT_FOUND


auth = AccessControl[BillingPermission](
    providers=[
        StaticProvider(
            {"t-1": Identity.user("user:1", permissions={BillingPermission.ISSUE})}
        )
    ]
)
billing = UseFramework("billing", log_after_execution=False)


@billing.feature(CommandIssueInvoice)
@auth.requires(BillingPermission.ISSUE)
@rest.post("/invoices", status=201, location="/invoices/{number}")
class IssueInvoice(Feature):
    """Issue an invoice."""

    def execute(self, dto: CommandIssueInvoice) -> Issued:
        return Issued(number=f"F-{dto.total}")


@billing.feature(QueryInvoice)
@auth.public
@rest.get("/invoices/{invoice_id}")
class GetInvoice(Feature):
    def execute(self, dto: QueryInvoice) -> Invoice:
        if dto.invoice_id == "F-404":
            raise InvoiceNotFound(f"invoice {dto.invoice_id} does not exist")
        return Invoice(invoice_id=dto.invoice_id)


@billing.feature(CommandCancelInvoice)
@auth.requires(BillingPermission.ISSUE)
@rest.post("/invoices/{invoice_id}/cancel")
class CancelInvoice(Feature):
    def execute(self, dto: CommandCancelInvoice) -> Invoice:
        return Invoice(invoice_id=dto.invoice_id, status=f"cancelled: {dto.why}")


@billing.feature(CommandRemoveInvoice)
@auth.requires(BillingPermission.ISSUE)
@rest.delete("/invoices/{invoice_id}")
class RemoveInvoice(Feature):
    def execute(self, dto: CommandRemoveInvoice) -> None:
        return None


auth.on(billing)

api = FastApiGateway([billing], title="Billing API")
api.group(billing, version="v1")  # /v1/billing/...
client = TestClient(api.app())
token = {"authorization": "Bearer t-1"}

created = client.post("/v1/billing/invoices", json={"total": 10}, headers=token)
assert created.status_code == 201
assert created.headers["location"] == "/v1/billing/invoices/F-10"

assert client.get("/v1/billing/invoices/F-10").json() == {
    "invoice_id": "F-10",
    "status": "issued",
}
cancelled = client.post(
    "/v1/billing/invoices/F-10/cancel", json={"why": "duplicate"}, headers=token
)
assert cancelled.json()["status"] == "cancelled: duplicate"

removed = client.delete("/v1/billing/invoices/F-10", headers=token)
assert (removed.status_code, removed.content) == (204, b"")
```

## Validate once, call the bus once

- A Command's body is annotated with its DTO. FastAPI validates it once and passes the instance
  on. Fields the path carries (`/invoices/{invoice_id}/cancel`) are merged into the body **before**
  that validation, and the path wins over the body. The document shows the body without them.
- A `Query` on `GET` (and a `DELETE`) is read by a dependency from the path and the query string,
  with a non-plain field such as `criteria` as JSON, and validated once.
- The route calls the bus. It never calls `execute`, and never the dictionary-in executor, which
  would validate again. That is also why `add(..., wrap=...)` is refused on this gateway: use an
  interceptor.
- Credentials are read in an async dependency, with no I/O. Authentication and `bus(dto)` then run
  **together in one worker thread** (`anyio.to_thread`). Identity is a `ContextVar`, and FastAPI
  runs each synchronous dependency in a copied context, so an identity set in one thread is lost
  in the next. `worker_threads=` bounds the pool (anyio's default is 40).

## Failures are problems

`install_problem_handlers(app)` answers every failure as `application/problem+json`, whether it
comes from a generated route, a hand-written one, FastAPI's validation or Starlette's 404/405:

| Kind | Status | Extra header |
|---|---|---|
| invalid | 422 (400 when the request cannot be read) | |
| unauthenticated | 401 | `WWW-Authenticate` |
| permission denied | 403 | |
| not found | 404 | |
| conflict | 409 | |
| in progress (idempotency) | 409 | `Retry-After` |
| key reused (idempotency) | 422 | |
| domain | 422 | |
| exhausted | 429 | `Retry-After` |
| unavailable | 503 | `Retry-After` |
| internal | 500, with no `detail` | |

Every problem also carries `kind`, `reason` (UPPER_SNAKE, stable: switch on it) and `trace_id`.
A 422 also carries `errors: [{pointer, detail}]`. In the document, FastAPI's `HTTPValidationError`
is replaced by `Problem`.

A project's own errors fit in by subclassing `DomainError` and declaring `failure_kind` on the
class, as `InvoiceNotFound` does above. `failure_kind` picks the status on any exception, but
only a `DomainError`'s message reaches the caller as `detail`. A plain `Exception` with
`failure_kind = "invalid"` answers 422 with no `detail`; without it, a 500. So the project
writes no `@app.exception_handler` of its own. One would answer a shape the OpenAPI
document does not promise, and the other wires (JSON-RPC, gRPC, MCP) would classify the same
error differently.

```python
missing = client.get("/v1/billing/invoices/F-404")
assert missing.status_code == 404
assert missing.headers["content-type"] == "application/problem+json"
problem = missing.json()
assert (problem["kind"], problem["reason"]) == ("not_found", "INVOICE_NOT_FOUND")

refused = client.post("/v1/billing/invoices", json={"total": 1})
assert refused.status_code == 401 and "www-authenticate" in refused.headers

invalid = client.post("/v1/billing/invoices", json={"total": "ten"}, headers=token)
assert invalid.json()["errors"][0]["pointer"] == "#/total"
```

## `Idempotency-Key`

The header reaches the bus context as `idempotency_key`. A Command's own `idempotency_key()` stays
authoritative. For a use case with `@idempotency.once` whose Command has no such method, the
header is **required**: a request without it gets a 400 (`IDEMPOTENCY_KEY_MISSING`), since
nothing else can tell a retry from a new request — and then the header is the run's key: two keys
are two runs, one key reused with other arguments is `KeyReused`. `KeyReused` answers 422, and
`AlreadyInProgress` answers 409 with `Retry-After`. The W3C `traceparent` header is passed through.
When the host's OpenTelemetry instrumentation opened a span, the bus span is its child instead.

## Full control

A project can take one context's router, leave out a Command and answer it with its own route.
That route uses the same helpers, so it runs the same path:

```python
app = FastAPI(separate_input_output_schemas=False)
install_problem_handlers(app)
app.include_router(api.router(billing, exclude=[CommandIssueInvoice]))
mine = APIRouter(prefix="/v1/billing")


@mine.post(
    "/invoices",
    status_code=201,
    operation_id="CommandIssueInvoice",
    responses=problem_responses(billing, CommandIssueInvoice),
    openapi_extra=operation_extra(billing, CommandIssueInvoice),
)
async def issue(cmd: CommandIssueInvoice, call: BusCall = Depends(bus_call(billing))) -> Issued:
    return await call(cmd)


app.include_router(mine)
assert api.verify(app) == []

hand_written = TestClient(app)
assert hand_written.post("/v1/billing/invoices", json={"total": 3}, headers=token).json() == {
    "number": "F-3"
}
assert hand_written.post("/v1/billing/invoices", json={"total": 3}).status_code == 401

document = app.openapi()
issue_doc = document["paths"]["/v1/billing/invoices"]["post"]
assert issue_doc["x-sincpro-requires"] == ["billing.invoice.issue"]
assert document["components"]["securitySchemes"] == {
    "static": {"type": "http", "scheme": "bearer"}
}
print(json.dumps(issue_doc["responses"]["401"]))
```

What is exported for full control: `bus_call`, `acting_credentials` (async, no I/O),
`request_context` (the `Idempotency-Key` and trace headers), `install_problem_handlers`,
`problem_responses`, `operation_extra`, and `api.verify(app)`. `verify` walks the app's routes and
reports generated and hand-written routes that collide, repeated `operationId`s, and a missing
`install_problem_handlers`. A FastAPI dependency is never authorization: `AccessControl` inside
the bus stays the only guard. `BackgroundTasks` must not carry domain work, because it runs after
the answer, outside idempotency.

`api.router(bus)` already carries the group's prefix and version. A `prefix=` passed to
`include_router` is only an extra mount point, and `Location` headers include it.

## A DTO that cannot travel as JSON

A Command with a `bytes` field (a PDF, an image) has no JSON body. What a wire carries is its
port's (`Wire.carries_bytes`), and this one carries JSON, so it generates no route for it.
Declaring `@rest.post` on its handler is refused: `verify()` names it and `app()` raises
`ExposureRefused`. A `bytes` Command with no binding is skipped with one log line, `Skipping
non-JSON Feature/ApplicationService [Command…]`.

The Command stays as it is, and so does its Feature. The route that accepts the file as base64
is written by hand in `entrypoints/`. It translates the body into the Command and calls the bus
through `bus_call`, the same path a generated route runs:

```python
import base64
import binascii


class CommandMeasureDocument(DataTransferObject):
    content: bytes


class Measured(DataTransferObject):
    size_bytes: int


class DocumentUnreadable(DomainError):
    failure_kind = FailureKind.INVALID


documents = UseFramework("documents", log_after_execution=False)


@documents.feature(CommandMeasureDocument)
class MeasureDocument(Feature):
    def execute(self, dto: CommandMeasureDocument) -> Measured:
        return Measured(size_bytes=len(dto.content))


class DocumentBody(DataTransferObject):
    """What the client sends — HTTP's shape, never the Command's."""

    content_base64: str


uploads = APIRouter(prefix="/v1/documents", tags=["documents"])


@uploads.post(
    "/measure",
    operation_id="CommandMeasureDocument",
    responses=problem_responses(documents, CommandMeasureDocument),
)
async def measure(body: DocumentBody, call: BusCall = Depends(bus_call(documents))) -> Measured:
    try:
        content = base64.b64decode(body.content_base64, validate=True)
    except binascii.Error as error:
        raise DocumentUnreadable("content_base64 is not valid base64") from error
    return await call(CommandMeasureDocument(content=content))


documents_api = FastApiGateway({"documents": documents}, unguarded=True)
documents_app = documents_api.app()
documents_app.include_router(uploads)
assert documents_api.verify(documents_app) == []

uploading = TestClient(documents_app)
encoded = base64.b64encode(b"%PDF-1.7").decode()
assert uploading.post("/v1/documents/measure", json={"content_base64": encoded}).json() == {
    "size_bytes": 8
}
unreadable = uploading.post("/v1/documents/measure", json={"content_base64": "@@"})
assert unreadable.status_code == 422
assert unreadable.json()["detail"] == "content_base64 is not valid base64"
```

- The route translates and calls; it decides nothing. Two use cases chained together (resolve
  the document, then read it) are an ApplicationService in `services/`, and the route calls that
  one.
- The bus still runs the Command in-process, from a test, a cron or another context, with the
  bytes as they are. Only HTTP needed the base64.
- `documents_api.app()` installed the problem handlers, so the `DomainError` raised in the
  route answers RFC 9457 like any refusal from the bus.

## Not yet

- The paged envelope for list Queries (`items`, `next_page_token`): phase 2.
- `ETag` / `If-Match`, and `body="merge-patch"`: phase 2. A binding that declares
  `concurrency="if-match"` or `body="merge-patch"` is refused. Accepting an `If-Match` without
  checking it would be a lie.
- Deprecation headers (`Deprecation`, `Sunset`): phase 2. The document already marks a deprecated
  binding `deprecated: true`.
