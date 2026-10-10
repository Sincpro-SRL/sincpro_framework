"""REST on FastAPI — the surface and its document (PRD_15 §2, §6).

The incidents: a use case public by forgetting (the resource profile publishes only what is
declared); two operations on one path, one silently shadowing the other; a hand-written route
that collides with a generated one; a 201 without its `Location`, a 204 with a body; a document
whose references point nowhere, whose `operationId`s repeat, or that promises FastAPI's error
shape while the API answers problems — so no generated client works.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from sincpro_framework import DataTransferObject, Feature, ProgrammingError, UseFramework
from sincpro_framework.auth import AccessControl, Identity, Permission, StaticProvider
from sincpro_framework.ddd.query import Query
from sincpro_framework.entrypoints.adapters.fastapi import (
    BusCall,
    FastApiGateway,
    bus_call,
    install_problem_handlers,
    operation_extra,
    problem_responses,
)
from sincpro_framework.entrypoints.entrypoint.decorators import rest


class Perm(Permission):
    ISSUE = "billing.invoice.issue"


class CommandIssueInvoice(DataTransferObject):
    total: int


class Issued(DataTransferObject):
    number: str


class QueryInvoice(Query):
    invoice_id: str


class Invoice(DataTransferObject):
    invoice_id: str
    note: str = ""


class QueryInvoices(Query):
    customer: str | None = None


class Invoices(DataTransferObject):
    customer: str | None
    limit: int


class CommandCancelInvoice(DataTransferObject):
    invoice_id: str
    why: str


class CommandRemoveInvoice(DataTransferObject):
    invoice_id: str


class CommandReconcile(DataTransferObject):
    """Never declared for REST — it stays off the resource profile."""


def _billing(auth: AccessControl[Perm] | None = None) -> UseFramework:
    bus = UseFramework("billing", log_after_execution=False)

    def access(cls: type) -> type:
        return auth.requires(Perm.ISSUE)(cls) if auth else cls

    def public(cls: type) -> type:
        return auth.public(cls) if auth else cls

    @bus.feature(CommandIssueInvoice)
    @access
    @rest.post("/invoices", status=201, location="/invoices/{number}")
    class IssueInvoice(Feature):
        """Issue an invoice."""

        def execute(self, dto: CommandIssueInvoice) -> Issued:
            return Issued(number=f"F-{dto.total}")

    @bus.feature(QueryInvoice)
    @public
    @rest.get("/invoices/{invoice_id}")
    class GetInvoice(Feature):
        def execute(self, dto: QueryInvoice) -> Invoice:
            return Invoice(invoice_id=dto.invoice_id)

    @bus.feature(QueryInvoices)
    @public
    @rest.get("/invoices")
    class ListInvoices(Feature):
        def execute(self, dto: QueryInvoices) -> Invoices:
            return Invoices(customer=dto.customer, limit=dto.criteria.limit)

    @bus.feature(CommandCancelInvoice)
    @access
    @rest.post("/invoices/{invoice_id}/cancel")
    class CancelInvoice(Feature):
        def execute(self, dto: CommandCancelInvoice) -> Invoice:
            return Invoice(invoice_id=dto.invoice_id, note=dto.why)

    @bus.feature(CommandRemoveInvoice)
    @access
    @rest.delete("/invoices/{invoice_id}")
    class RemoveInvoice(Feature):
        def execute(self, dto: CommandRemoveInvoice) -> None:
            return None

    @bus.feature(CommandReconcile)
    @access
    class Reconcile(Feature):
        def execute(self, dto: CommandReconcile) -> None:
            return None

    if auth is not None:
        auth.on(bus)
    return bus


@pytest.fixture
def api() -> FastApiGateway:
    gateway = FastApiGateway([_billing()], unguarded=True)
    gateway.group("billing", version="v1")
    return gateway


@pytest.fixture
def client(api: FastApiGateway) -> TestClient:
    return TestClient(api.app())


# The surface


def test_the_resource_profile_publishes_only_what_is_declared(api: FastApiGateway) -> None:
    names = {entry.name for entry in api.manifest()}
    assert "CommandReconcile" not in names
    assert names == {
        "CommandIssueInvoice",
        "QueryInvoice",
        "QueryInvoices",
        "CommandCancelInvoice",
        "CommandRemoveInvoice",
    }


def test_the_rpc_profile_is_the_catalog_on_todays_paths() -> None:
    bus = UseFramework("billing", log_after_execution=False)

    @bus.feature(CommandIssueInvoice)
    class IssueInvoice(Feature):
        def execute(self, dto: CommandIssueInvoice) -> Issued:
            return Issued(number=f"F-{dto.total}")

    @bus.feature(QueryInvoices)
    class ListInvoices(Feature):
        def execute(self, dto: QueryInvoices) -> Invoices:
            return Invoices(customer=dto.customer, limit=dto.criteria.limit)

    @bus.feature(CommandReconcile)
    class Reconcile(Feature):
        def execute(self, dto: CommandReconcile) -> None:
            return None

    api = FastApiGateway([bus], profile="rpc", unguarded=True)
    client = TestClient(api.app())
    assert client.post("/billing/command-reconcile").status_code == 204
    issued = client.post("/billing/command-issue-invoice", json={"total": 3})
    assert issued.json() == {"number": "F-3"}
    criteria = '{"pagination": {"limit": 4}}'
    by_url = client.get("/billing/query-invoices", params={"criteria": criteria})
    by_body = client.post(
        "/billing/query-invoices", json={"criteria": {"pagination": {"limit": 4}}}
    )
    assert by_url.json() == by_body.json() == {"customer": None, "limit": 4}
    ids = _operation_ids(api.app().openapi())
    assert {"QueryInvoices", "QueryInvoices_by_body"} <= set(ids)


def test_a_declared_path_wins_over_the_rpc_profiles_derived_one() -> None:
    api = FastApiGateway([_billing()], profile="rpc", unguarded=True)
    client = TestClient(api.app())
    assert client.post("/billing/invoices", json={"total": 2}).status_code == 201
    assert client.post("/billing/command-reconcile").status_code == 204


def test_a_create_answers_201_with_its_location_under_every_prefix(
    api: FastApiGateway,
) -> None:
    app = FastAPI(separate_input_output_schemas=False)
    install_problem_handlers(app)
    app.include_router(api.router(), prefix="/api")
    created = TestClient(app).post("/api/v1/billing/invoices", json={"total": 7})
    assert created.status_code == 201
    assert created.headers["location"] == "/api/v1/billing/invoices/F-7"
    assert created.json() == {"number": "F-7"}


def test_a_removal_answers_204_with_no_body(client: TestClient) -> None:
    removed = client.delete("/v1/billing/invoices/F-1")
    assert removed.status_code == 204
    assert removed.content == b""


def test_path_fields_merge_into_the_dto_and_the_path_wins(client: TestClient) -> None:
    cancelled = client.post(
        "/v1/billing/invoices/F-2/cancel", json={"why": "duplicate", "invoice_id": "F-9"}
    )
    assert cancelled.json() == {"invoice_id": "F-2", "note": "duplicate"}


def test_a_query_reads_its_path_and_its_criteria_as_json(client: TestClient) -> None:
    assert client.get("/v1/billing/invoices/F-3").json() == {"invoice_id": "F-3", "note": ""}
    listed = client.get(
        "/v1/billing/invoices",
        params={"customer": "c-1", "criteria": '{"pagination": {"limit": 5}}'},
    )
    assert listed.json() == {"customer": "c-1", "limit": 5}


def test_two_operations_on_one_method_and_path_are_refused() -> None:
    bus = UseFramework("clash", log_after_execution=False)

    @bus.feature(QueryInvoice)
    @rest.get("/invoices/{invoice_id}")
    class GetInvoice(Feature):
        def execute(self, dto: QueryInvoice) -> Invoice:
            return Invoice(invoice_id=dto.invoice_id)

    class QueryInvoiceByNumber(Query):
        number: str

    @bus.feature(QueryInvoiceByNumber)
    @rest.get("/invoices/{number}")
    class GetByNumber(Feature):
        def execute(self, dto: QueryInvoiceByNumber) -> Invoice:
            return Invoice(invoice_id=dto.number)

    api = FastApiGateway([bus], unguarded=True)
    with pytest.raises(ProgrammingError, match="GET /clash/invoices/.* answers both"):
        api.app()


def test_one_name_on_two_contexts_is_qualified_by_its_group() -> None:
    sales = UseFramework("sales", log_after_execution=False)

    @sales.feature(CommandIssueInvoice)
    class IssueSalesInvoice(Feature):
        def execute(self, dto: CommandIssueInvoice) -> Issued:
            return Issued(number="S-1")

    api = FastApiGateway([_billing(), sales], profile="rpc", unguarded=True)
    ids = _operation_ids(api.app().openapi())
    assert {"billing_CommandIssueInvoice", "sales_CommandIssueInvoice"} <= set(ids)
    assert len(ids) == len(set(ids))


def test_what_is_served_only_in_phase_2_is_refused_not_ignored() -> None:
    bus = UseFramework("later", log_after_execution=False)

    @bus.feature(CommandCancelInvoice)
    @rest.patch("/invoices/{invoice_id}", concurrency="if-match")
    class Patch(Feature):
        def execute(self, dto: CommandCancelInvoice) -> Invoice:
            return Invoice(invoice_id=dto.invoice_id)

    problems = FastApiGateway([bus], unguarded=True).verify()
    assert any("if-match" in one for one in problems)


def test_wrap_is_refused_because_this_wire_never_calls_the_wrapped_run() -> None:
    with pytest.raises(ProgrammingError, match="wrap"):
        FastApiGateway().add(_billing(), wrap={CommandIssueInvoice: lambda run: run})


def test_verify_finds_a_hand_written_route_colliding_with_a_generated_one(
    api: FastApiGateway,
) -> None:
    bus = api.buses()[0]
    app = FastAPI(separate_input_output_schemas=False)
    install_problem_handlers(app)
    app.include_router(api.router(bus))
    mine = APIRouter(prefix="/v1/billing")

    @mine.get("/invoices/{number}", operation_id="MyInvoice")
    async def mine_get(number: str, call: BusCall = Depends(bus_call(bus))) -> Any:
        return await call(QueryInvoice(invoice_id=number))

    app.include_router(mine)
    assert any(
        "GET /v1/billing/invoices/{number} answered twice" in one for one in api.verify(app)
    )

    bare = FastAPI()
    bare.include_router(api.router(bus, exclude=[QueryInvoice]))
    bare.include_router(mine)
    problems = api.verify(bare)
    assert not any("answered twice" in one for one in problems)
    assert any("install_problem_handlers" in one for one in problems)


# The document


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


def _operation_ids(document: dict[str, Any]) -> list[str]:
    return [
        operation["operationId"]
        for item in document["paths"].values()
        for operation in item.values()
    ]


def _guarded_api() -> FastApiGateway:
    auth = AccessControl[Perm](
        providers=[StaticProvider({"t-1": Identity.user("user:1", permissions={Perm.ISSUE})})]
    )
    api = FastApiGateway([_billing(auth)])
    api.group("billing", version="v1")
    return api


def test_the_document_validates_and_every_reference_resolves() -> None:
    document = _guarded_api().app().openapi()
    try:
        from openapi_spec_validator import validate  # pyright: ignore[reportMissingImports]
    except ImportError:
        validate = None
    if validate is not None:
        validate(document)
    assert document["openapi"].startswith("3.1")
    models = document["components"]["schemas"]
    for reference in _references(document):
        assert reference.startswith("#/components/schemas/"), reference
        assert reference.rsplit("/", 1)[-1] in models, reference
    ids = _operation_ids(document)
    assert len(ids) == len(set(ids))
    for item in document["paths"].values():
        for operation in item.values():
            assert set(operation["responses"]) >= {"400", "422", "500"}
            for status, answer in operation["responses"].items():
                if status.startswith(("4", "5")):
                    assert list(answer["content"]) == ["application/problem+json"]


def test_fastapis_validation_error_shape_is_replaced_by_problem() -> None:
    api = _guarded_api()
    app = FastAPI(separate_input_output_schemas=False)
    install_problem_handlers(app)
    app.include_router(api.router())

    @app.post("/mine")
    async def mine(dto: CommandIssueInvoice) -> Issued:
        return Issued(number="M-1")

    document = app.openapi()
    assert "HTTPValidationError" not in document["components"]["schemas"]
    refused = document["paths"]["/mine"]["post"]["responses"]["422"]
    assert refused["content"] == {
        "application/problem+json": {"schema": {"$ref": "#/components/schemas/Problem"}}
    }
    assert {"type", "title", "status", "kind", "reason"} <= set(
        document["components"]["schemas"]["Problem"]["required"]
    )


def test_the_document_says_who_may_call_each_operation() -> None:
    document = _guarded_api().app().openapi()
    issue = document["paths"]["/v1/billing/invoices"]["post"]
    assert issue["operationId"] == "CommandIssueInvoice"
    assert issue["x-sincpro-requires"] == ["billing.invoice.issue"]
    assert issue["security"] == [{"static": []}]
    assert {"401", "403"} <= set(issue["responses"])
    assert document["components"]["securitySchemes"] == {
        "static": {"type": "http", "scheme": "bearer"}
    }
    assert all("x-sincpro-security-schemes" not in op for op in _operations(document))
    listed = document["paths"]["/v1/billing/invoices"]["get"]
    assert listed["security"] == []
    assert "401" not in listed["responses"]


def _operations(document: dict[str, Any]) -> list[dict[str, Any]]:
    return [one for item in document["paths"].values() for one in item.values()]


def test_the_body_omits_what_the_path_carries_and_both_are_documented() -> None:
    document = _guarded_api().app().openapi()
    cancel = document["paths"]["/v1/billing/invoices/{invoice_id}/cancel"]["post"]
    body = cancel["requestBody"]["content"]["application/json"]["schema"]
    assert set(body["properties"]) == {"why"}
    assert "$ref" not in body
    assert [(one["name"], one["in"]) for one in cancel["parameters"]] == [
        ("invoice_id", "path")
    ]
    listed = document["paths"]["/v1/billing/invoices"]["get"]
    by_name = {one["name"]: one for one in listed["parameters"]}
    assert by_name["customer"]["in"] == "query"
    assert "application/json" in by_name["criteria"]["content"]
    removed = document["paths"]["/v1/billing/invoices/{invoice_id}"]["delete"]
    assert "204" in removed["responses"]


def test_a_hand_written_route_documents_itself_like_a_generated_one() -> None:
    api = _guarded_api()
    bus = api.buses()[0]
    extra = operation_extra(bus, CommandIssueInvoice)
    assert extra["x-sincpro-requires"] == ["billing.invoice.issue"]
    assert {401, 403, 422, 500} <= set(problem_responses(bus, CommandIssueInvoice))


# --- a Query is read with GET, however it inherits Query ------------------------------------


class QueryWithCriteria(Query):
    """A project's own base for its listing Queries — a Query by inheritance, not by name."""

    tenant: str = ""


class ListCustomers(QueryWithCriteria):
    """Named without the `Query` prefix: the method comes from the class, never the name."""


class Customers(DataTransferObject):
    names: list[str] = []


class CommandRenameCustomer(DataTransferObject):
    name: str


def _customers() -> UseFramework:
    bus = UseFramework("crm", log_after_execution=False)

    @bus.feature(ListCustomers)
    class List(Feature):
        def execute(self, dto: ListCustomers) -> Customers:
            return Customers(names=[f"{dto.tenant}:ana"])

    @bus.feature(CommandRenameCustomer)
    class Rename(Feature):
        def execute(self, dto: CommandRenameCustomer) -> Customers:
            return Customers(names=[dto.name])

    return bus


def _methods_of(app: FastAPI) -> dict[str, set[str]]:
    """Each published path with its methods, as the OpenAPI document tells a client."""
    return {
        path: {method.upper() for method in operations}
        for path, operations in app.openapi()["paths"].items()
        if path.startswith("/crm")
    }


def test_anything_that_inherits_query_is_derived_get_and_a_command_post():
    from sincpro_framework.entrypoints import Exposure

    api = FastApiGateway([_customers()], exposure=Exposure.CATALOG, unguarded=True)
    client = TestClient(api.app())

    assert _methods_of(api.app()) == {
        "/crm/list-customers": {"GET"},
        "/crm/rename-customer": {"POST"},
    }
    assert client.get("/crm/list-customers", params={"tenant": "acme"}).json() == {
        "names": ["acme:ana"]
    }


def test_the_rpc_profile_reads_an_inherited_query_with_get_and_post():
    """POST too, for a criteria too long for a URL — GET is always there."""
    api = FastApiGateway([_customers()], profile="rpc", unguarded=True)

    assert _methods_of(api.app()) == {
        "/crm/list-customers": {"GET", "POST"},
        "/crm/command-rename-customer": {"POST"},  # today's path: the whole DTO name
    }
