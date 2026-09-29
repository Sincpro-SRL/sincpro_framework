# REST: the buses' use cases as HTTP resources, with OpenAPI 3.1

`RestGateway` publishes every Feature and ApplicationService of its buses as an HTTP route and
describes them in an OpenAPI 3.1 document — so any client generator (orval, openapi-typescript),
API gateway or tool reads them. It needs the `[rest]` extra to serve; the route table and the
document are built without it.

Every block on this page runs, in order, in `tests/docs/test_persistence_guide.py`.

## The mapping

| The use case's DTO | Route | Why |
|---|---|---|
| a Command | `POST /{alias}/{kebab-name}` — the body is the DTO | writes are never cached, never repeated by a proxy |
| a `Query` (`sincpro_framework.ddd.query.Query`) | `GET /{alias}/{kebab-name}?field=…&criteria=<json>` | reads are cacheable; the criteria is the JSON `@sincpro/criteria`'s `pack()` writes |
| a `Query` whose criteria is too long for a URL | `POST` to the same path, the fields in the body | the way `_search` is answered on both |
| a path of the project's | `rest.route(Dto, "GET /billing/invoices/{invoice_id}")` | the path's names are the DTO's fields |

The path never carries the layer: a Feature that becomes an ApplicationService keeps its URL.

```python
from starlette.testclient import TestClient

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.ddd.query import Query
from sincpro_framework.entrypoints import internal
from sincpro_framework.entrypoints.rest import RestGateway


class CommandIssueInvoice(DataTransferObject):
    total: int


class Issued(DataTransferObject):
    number: str


class QueryInvoices(Query):
    customer: str | None = None


class Invoices(DataTransferObject):
    customer: str | None
    limit: int


class CommandReconcile(DataTransferObject):
    pass


billing = UseFramework("billing", log_after_execution=False)


@billing.feature(CommandIssueInvoice)
class IssueInvoice(Feature):
    """Issue an invoice."""

    def execute(self, dto: CommandIssueInvoice) -> Issued:
        return Issued(number="F-1")


@billing.feature(QueryInvoices)
class ListInvoices(Feature):
    def execute(self, dto: QueryInvoices) -> Invoices:
        return Invoices(customer=dto.customer, limit=dto.criteria.limit)


@billing.feature(CommandReconcile)
@internal
class Reconcile(Feature):
    def execute(self, dto: CommandReconcile) -> None: ...


rest = RestGateway([billing], prefix="/api/v1", title="Billing API")
client = TestClient(rest.app())

assert client.post("/api/v1/billing/command-issue-invoice", json={"total": 10}).json() == {
    "number": "F-1"
}
listed = client.get(
    "/api/v1/billing/query-invoices",
    params={"customer": "c-1", "criteria": '{"pagination": {"limit": 5}}'},
)
assert listed.json() == {"customer": "c-1", "limit": 5}
assert client.post("/api/v1/billing/command-reconcile").status_code == 404
```

`CommandReconcile` is `internal`: it runs in the process — a cron, another use case — and is on
no wire.

## The document

```python
document = rest.openapi(servers=["https://api.sincpro.com.bo"])
assert document["openapi"] == "3.1.0"
operation = document["paths"]["/api/v1/billing/command-issue-invoice"]["post"]
assert operation["operationId"] == "CommandIssueInvoice"
assert operation["tags"] == ["billing", "features"]
assert "Criteria" in document["components"]["schemas"]
```

- `operationId` is the DTO's name — a generated client calls `commandIssueInvoice(...)`, the name
  the logs, JSON-RPC and MCP use. A name two buses answer is qualified by the alias.
- Every model is under `components/schemas`, lifted from each DTO's `$defs`, once.
- A guarded bus adds `securitySchemes` from its providers and, on each operation, `security` and
  `x-sincpro-requires` — the permissions it declared.
- `GET /openapi.json` serves it and `GET /docs` a Swagger UI page over it.

## Failures

| Raised | Status | `error.kind` |
|---|---|---|
| a body or query value that is not JSON | 400 | `invalid` |
| the DTO refused by validation | 422 | `invalid`, with `detail` |
| `Unauthenticated` / `PermissionDenied` | 401 with `WWW-Authenticate` / 403 | `unauthenticated` / `permission_denied` |
| `StaleAggregate`, `DuplicateAggregate` | 409 | `conflict` |
| another `DomainError` | 422 | `domain`, with its message |
| anything else | 500 | `internal` — the message stays in the log |

## Into an app of the project's

```python
from starlette.applications import Starlette
from starlette.routing import Mount

app = Starlette(routes=[Mount("/public", routes=rest.routes(docs_path=None))])
assert TestClient(app).get("/public/openapi.json").status_code == 200
```

`routes()` is the composable primitive: mount it, namespace it, wrap it with the project's
middleware — CORS, `IdentityMiddleware`, a rate limit — or add it to a FastAPI app's router.
`app(middleware=..., routes=..., lifespan=...)` is those routes served alone; `run()` starts it
with uvicorn. See [integration](integration.md) for what every gateway shares.
