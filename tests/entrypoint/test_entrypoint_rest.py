"""The REST wire: every use case of N buses as an HTTP resource with an OpenAPI document — and the
integration every gateway shares: automatic mode, include / exclude / layers / `internal`, and the
app or routes handed back for the project's own middleware.

The incidents: a use case published that was meant to stay inside; a URL that changes when a
Feature becomes an ApplicationService; an OpenAPI document whose references point nowhere, so no
client generates; a failure answered with the inside of the process.
"""

import json
from collections.abc import Iterator
from typing import Any

import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.routing import Mount
from starlette.testclient import TestClient

from sincpro_framework import ApplicationService, DataTransferObject, Feature, UseFramework
from sincpro_framework.auth import AccessControl, Identity, Permission, StaticProvider
from sincpro_framework.ddd.exceptions import DomainError, StaleAggregate
from sincpro_framework.ddd.query import Query
from sincpro_framework.entrypoints.exposure import internal
from sincpro_framework.entrypoints.grpc import GrpcGateway
from sincpro_framework.entrypoints.mcp import McpGateway
from sincpro_framework.entrypoints.rest import RestGateway, kebab
from sincpro_framework.entrypoints.rpc import RpcGateway


class Perm(Permission):
    ISSUE = "billing.invoice.issue"


class CommandIssueInvoice(DataTransferObject):
    total: int
    customer: str = "c-1"


class Issued(DataTransferObject):
    number: str
    total: int


class QueryInvoices(Query):
    customer: str | None = None


class Invoices(DataTransferObject):
    customer: str | None
    limit: int


class QueryInvoice(DataTransferObject):
    invoice_id: str


class Invoice(DataTransferObject):
    invoice_id: str


class CommandReconcile(DataTransferObject):
    pass


class CommandCheckout(DataTransferObject):
    total: int


class CommandFail(DataTransferObject):
    how: str


class CommandListOrders(DataTransferObject):
    pass


class Orders(DataTransferObject):
    count: int


def _billing(auth: AccessControl[Perm] | None = None) -> UseFramework:
    billing = UseFramework("sincpro-billing", log_after_execution=False)

    def guarded(cls: type) -> type:
        return auth.requires(Perm.ISSUE)(cls) if auth else cls

    @billing.feature(CommandIssueInvoice)
    @guarded
    class IssueInvoice(Feature):
        """Issue an invoice for a customer."""

        def execute(self, dto: CommandIssueInvoice) -> Issued:
            return Issued(number="F-1", total=dto.total)

    @billing.feature(QueryInvoices)
    class ListInvoices(Feature):
        def execute(self, dto: QueryInvoices) -> Invoices:
            return Invoices(customer=dto.customer, limit=dto.criteria.limit)

    @billing.feature(QueryInvoice)
    class GetInvoice(Feature):
        def execute(self, dto: QueryInvoice) -> Invoice:
            return Invoice(invoice_id=dto.invoice_id)

    @billing.feature(CommandReconcile)
    @internal
    class Reconcile(Feature):
        def execute(self, dto: CommandReconcile) -> None: ...

    @billing.app_service(CommandCheckout)
    class Checkout(ApplicationService):
        def execute(self, dto: CommandCheckout) -> Issued | None:
            return self.feature_bus.execute(CommandIssueInvoice(total=dto.total), Issued)

    @billing.feature(CommandFail)
    class Fail(Feature):
        def execute(self, dto: CommandFail) -> None:
            if dto.how == "stale":
                raise StaleAggregate("invoice F-1 was written by someone else")
            if dto.how == "domain":
                raise DomainError("an invoice has to balance")
            raise RuntimeError("postgres://admin:hunter2@db — never to the caller")

    if auth is not None:
        auth.on(billing)
    return billing


def _sales() -> UseFramework:
    sales = UseFramework("sales", log_after_execution=False)

    @sales.feature(CommandListOrders)
    class ListOrders(Feature):
        def execute(self, dto: CommandListOrders) -> Orders:
            return Orders(count=3)

    @sales.feature(CommandIssueInvoice)
    class IssueSalesInvoice(Feature):
        def execute(self, dto: CommandIssueInvoice) -> Issued:
            return Issued(number="S-1", total=dto.total)

    return sales


@pytest.fixture
def client() -> Iterator[TestClient]:
    rest = RestGateway([_billing(), _sales()], prefix="/api/v1")
    rest.route(QueryInvoice, "GET /billing/invoices/{invoice_id}")
    yield TestClient(rest.app())


# The routes


def test_names_become_paths_in_kebab_case() -> None:
    assert kebab("CommandIssueInvoice") == "command-issue-invoice"
    assert kebab("HTTPRequestLog") == "http-request-log"
    assert kebab("Query2Invoices") == "query2-invoices"


def test_automatic_mode_publishes_every_bus_under_its_name(client: TestClient) -> None:
    issued = client.post("/api/v1/sincpro-billing/command-issue-invoice", json={"total": 10})
    assert issued.json() == {"number": "F-1", "total": 10}
    sold = client.post("/api/v1/sales/command-list-orders")
    assert sold.json() == {"count": 3}


def test_a_query_is_a_get_its_criteria_json_in_the_query_string(client: TestClient) -> None:
    criteria = json.dumps({"pagination": {"limit": 7}})
    got = client.get(
        "/api/v1/sincpro-billing/query-invoices",
        params={"customer": "c-9", "criteria": criteria},
    )
    assert got.json() == {"customer": "c-9", "limit": 7}
    by_body = client.post(
        "/api/v1/sincpro-billing/query-invoices",
        json={"customer": "c-9", "criteria": {"pagination": {"limit": 7}}},
    )
    assert by_body.json() == got.json()


def test_a_command_is_never_a_get(client: TestClient) -> None:
    assert client.get("/api/v1/sincpro-billing/command-issue-invoice").status_code == 405


def test_a_route_of_the_projects_takes_its_fields_from_the_path(client: TestClient) -> None:
    assert client.get("/api/v1/billing/invoices/F-7").json() == {"invoice_id": "F-7"}


def test_the_layer_is_never_in_the_url(client: TestClient) -> None:
    checkout = client.post("/api/v1/sincpro-billing/command-checkout", json={"total": 5})
    assert checkout.json() == {"number": "F-1", "total": 5}


def test_two_use_cases_on_one_path_are_refused() -> None:
    rest = RestGateway([_billing()])
    rest.route(QueryInvoice, "POST /sincpro-billing/command-issue-invoice")
    with pytest.raises(ValueError, match="answers both"):
        rest.rest_routes()


# What a failure answers


@pytest.mark.parametrize(
    ("body", "status", "kind"),
    [
        ({"how": "stale"}, 409, "conflict"),
        ({"how": "domain"}, 422, "domain"),
        ({"how": "boom"}, 500, "internal"),
        ({}, 422, "invalid"),
    ],
)
def test_each_failure_answers_its_status_and_never_the_inside(
    client: TestClient, body: dict[str, Any], status: int, kind: str
) -> None:
    answered = client.post("/api/v1/sincpro-billing/command-fail", json=body)
    assert (answered.status_code, answered.json()["error"]["kind"]) == (status, kind)
    assert "hunter2" not in answered.text


def test_a_body_that_is_not_json_is_a_400(client: TestClient) -> None:
    answered = client.post(
        "/api/v1/sincpro-billing/command-issue-invoice",
        content=b"{not json",
        headers={"content-type": "application/json"},
    )
    assert answered.status_code == 400


# Auth


def test_rest_authenticates_like_every_wire_and_documents_what_is_required() -> None:
    auth = AccessControl[Perm](
        providers=[StaticProvider({"t-1": Identity.user("user:1", permissions={Perm.ISSUE})})]
    )
    rest = RestGateway([_billing(auth)])
    client = TestClient(rest.app())
    path = "/sincpro-billing/command-issue-invoice"
    assert client.post(path, json={"total": 1}).status_code == 401
    ok = client.post(path, json={"total": 1}, headers={"authorization": "Bearer t-1"})
    assert ok.status_code == 200
    operation = rest.openapi()["paths"][path]["post"]
    assert operation["x-sincpro-requires"] == ["billing.invoice.issue"]
    assert operation["security"] == [{"static": []}]
    schemes = rest.openapi()["components"]["securitySchemes"]
    assert schemes == {"static": {"type": "http", "scheme": "bearer"}}


# The OpenAPI document


def _references(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref":
                yield value
            else:
                yield from _references(value)
    elif isinstance(node, list):
        for one in node:
            yield from _references(one)


def test_every_reference_of_the_document_points_to_a_model_it_has() -> None:
    rest = RestGateway([_billing(), _sales()])
    document = rest.openapi(servers=["https://api.sincpro.com.bo"])
    assert document["openapi"] == "3.1.0"
    models = document["components"]["schemas"]
    for reference in _references(document["paths"]):
        assert reference.startswith("#/components/schemas/")
        assert reference.rsplit("/", 1)[-1] in models, reference
    for reference in _references(models):
        assert reference.rsplit("/", 1)[-1] in models, reference


def test_an_operation_id_is_unique_even_for_one_name_on_two_buses() -> None:
    document = RestGateway([_billing(), _sales()]).openapi()
    ids = [
        operation["operationId"]
        for item in document["paths"].values()
        for operation in item.values()
    ]
    assert len(ids) == len(set(ids))
    assert {"sincpro-billing_CommandIssueInvoice", "sales_CommandIssueInvoice"} <= set(ids)


def test_a_query_is_documented_as_query_parameters_its_criteria_as_json() -> None:
    document = RestGateway([_billing()]).openapi()
    get = document["paths"]["/sincpro-billing/query-invoices"]["get"]
    by_name = {one["name"]: one for one in get["parameters"]}
    assert by_name["customer"]["schema"]["anyOf"][0]["type"] == "string"
    assert "application/json" in by_name["criteria"]["content"]


# What every gateway shares


def test_internal_is_on_no_wire() -> None:
    billing = _billing()
    assert not any(
        "reconcile" in route.path for route in RestGateway([billing]).rest_routes()
    )
    assert not any("CommandReconcile" in name for name in RpcGateway([billing]).methods())
    assert not any("CommandReconcile" in path for path in GrpcGateway([billing]).methods())
    assert "CommandReconcile" not in McpGateway([billing]).tool_names()
    assert billing(CommandReconcile()) is None


def test_exclude_include_and_layers_narrow_what_a_wire_publishes() -> None:
    narrowed = (
        RestGateway()
        .add(_billing(), exclude=[CommandFail])
        .add("ventas", _sales(), include=[CommandListOrders])
    )
    paths = {route.path for route in narrowed.rest_routes()}
    assert "/sincpro-billing/command-fail" not in paths
    assert (
        paths & {"/ventas/command-list-orders"}
        and "/ventas/command-issue-invoice" not in paths
    )
    apps_only = RestGateway([_billing()], layers=("app_services",))
    assert [route.path for route in apps_only.rest_routes()] == [
        "/sincpro-billing/command-checkout"
    ]


def test_each_wire_makes_a_buses_name_fit_for_it() -> None:
    billing = _billing()
    assert list(RpcGateway([billing]).catalogs) == ["sincpro-billing"]
    assert list(GrpcGateway([billing]).catalogs) == ["sincpro_billing"]
    assert list(RestGateway([billing]).catalogs) == ["sincpro-billing"]


def test_routes_mount_into_an_app_of_the_projects_with_its_middleware() -> None:
    seen: list[str] = []

    class Recording:
        def __init__(self, app: Any) -> None:
            self.app = app

        async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
            if scope["type"] == "http":
                seen.append(scope["path"])
            await self.app(scope, receive, send)

    rest = RestGateway([_sales()])
    app = Starlette(
        routes=[Mount("/api", routes=rest.routes(docs_path=None))],
        middleware=[Middleware(Recording)],
    )
    client = TestClient(app)
    assert client.post("/api/sales/command-list-orders").json() == {"count": 3}
    assert client.get("/api/openapi.json").json()["openapi"] == "3.1.0"
    assert seen == ["/api/sales/command-list-orders", "/api/openapi.json"]


def test_the_docs_page_and_the_health_check_are_served(client: TestClient) -> None:
    assert "swagger-ui" in client.get("/docs").text
    assert client.get("/healthz").json() == {"status": "ok"}
