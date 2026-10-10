"""JSON-RPC on declared exposure, with names that carry no layer (PRD_15 §4, PRD_14).

The incidents these protect: promoting a Feature to an ApplicationService renamed a public
method (`billing.features.X` became `billing.app_services.X`) and broke every client; every use
case of every bus was public the moment a gateway was built, so a use case nobody meant to
publish was one forgotten `exclude` away from the internet; a hand-written route that called the
bus its own way skipped the limits the generated one enforces.
"""

import json
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.testclient import TestClient

from sincpro_framework import (
    ApplicationService,
    DataTransferObject,
    Feature,
    ProgrammingError,
    UseFramework,
)
from sincpro_framework.auth import AccessControl, Identity, Permission, StaticProvider
from sincpro_framework.auth.entrypoint.transports import credentials_from_asgi
from sincpro_framework.common.store import InMemoryKeyValue
from sincpro_framework.data_layer.caching import Idempotency
from sincpro_framework.ddd import Query
from sincpro_framework.entrypoints import Exposure
from sincpro_framework.entrypoints.adapters.rpc import (
    JsonRpcWire,
    RpcGateway,
    RpcSurface,
    dispatch,
    http_status,
    operation_name,
)
from sincpro_framework.entrypoints.adapters.rpc.entrypoint import is_json_media_type
from sincpro_framework.entrypoints.adapters.rpc.errors import METHOD_NOT_FOUND
from sincpro_framework.entrypoints.domain.bindings import RpcBinding
from sincpro_framework.entrypoints.domain.surface import Wire
from sincpro_framework.entrypoints.entrypoint.decorators import rpc


class CommandIssueInvoice(DataTransferObject):
    customer_id: int


class Issued(DataTransferObject):
    number: str


class QueryInvoice(Query):
    invoice_id: str


class CommandReconcile(DataTransferObject):
    pass


class ValidateCard(DataTransferObject):
    card_number: str


def _billing(
    name: str = "billing",
    issue: Callable[[type], type] = rpc(),
    read: Callable[[type], type] = rpc(),
    build: bool = True,
) -> UseFramework:
    """`IssueInvoice` and `GetInvoice` bound for JSON-RPC by default; `Reconcile` never."""
    bus = UseFramework(name, log_after_execution=False)

    @bus.feature(CommandIssueInvoice)
    @issue
    class IssueInvoice(Feature):
        """Issue an invoice."""

        def execute(self, dto: CommandIssueInvoice) -> Issued:
            return Issued(number=f"F-{dto.customer_id}")

    @bus.feature(QueryInvoice)
    @read
    class GetInvoice(Feature):
        def execute(self, dto: QueryInvoice) -> Issued:
            return Issued(number=dto.invoice_id)

    @bus.feature(CommandReconcile)
    class Reconcile(Feature):
        def execute(self, dto: CommandReconcile) -> None:
            return None

    if build:
        bus.build_root_bus()
    return bus


def _call(method: str, params: dict[str, Any] | None = None, request_id: Any = 1) -> Any:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}


def _refused(gateway: RpcGateway) -> str:
    with pytest.raises(ProgrammingError) as refused:
        gateway.surface()
    return str(refused.value)


# --- names ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("dto_name", "operation"),
    [
        ("CommandIssueInvoice", "issue_invoice"),
        ("QueryInvoice", "invoice"),
        ("ValidateCard", "validate_card"),
        ("CommandCreateQREconomico", "create_qr_economico"),
        ("Commander", "commander"),
        ("Command", "command"),
    ],
)
def test_the_operation_is_the_dto_name_without_command_or_query_in_snake_case(
    dto_name: str, operation: str
) -> None:
    assert operation_name(dto_name) == operation


def test_a_declared_method_is_named_namespace_dot_operation_with_no_layer() -> None:
    gateway = RpcGateway({"billing": _billing()}, unguarded=True)

    assert set(gateway.methods()) == {"billing.issue_invoice", "billing.invoice"}
    answered = gateway.handle(_call("billing.issue_invoice", {"customer_id": 7}))
    assert answered == {"jsonrpc": "2.0", "id": 1, "result": {"number": "F-7"}}
    old = gateway.handle(_call("billing.features.CommandIssueInvoice", {"customer_id": 7}))
    assert isinstance(old, dict) and old["error"]["code"] == METHOD_NOT_FOUND


def test_the_name_is_unchanged_when_a_feature_becomes_an_application_service() -> None:
    """A Feature promoted to an ApplicationService is an internal change: no client sees it."""
    as_feature = _billing("as-feature")
    as_app_service = UseFramework("as-app-service", log_after_execution=False)

    @as_app_service.app_service(CommandIssueInvoice)
    @rpc()
    class IssueInvoiceService(ApplicationService):
        def execute(self, dto: CommandIssueInvoice) -> Issued:
            return Issued(number=f"S-{dto.customer_id}")

    as_app_service.build_root_bus()
    before = RpcGateway({"billing": as_feature}, unguarded=True)
    after = RpcGateway({"billing": as_app_service}, unguarded=True)

    assert "billing.issue_invoice" in before.methods()
    assert set(after.methods()) == {"billing.issue_invoice"}
    answered = after.handle(_call("billing.issue_invoice", {"customer_id": 3}))
    assert isinstance(answered, dict) and answered["result"] == {"number": "S-3"}
    document = after.discover()
    assert "app_services" not in str(document) and "features" not in str(document)


def test_the_group_namespace_carries_a_breaking_version() -> None:
    bus = _billing()
    by_namespace = RpcGateway({"billing": bus}, unguarded=True).group(
        bus, namespace="billing.v2"
    )
    by_version = RpcGateway({"billing": bus}, unguarded=True).group(bus, version="v2")

    assert set(by_namespace.methods()) == {"billing.v2.issue_invoice", "billing.v2.invoice"}
    assert set(by_version.methods()) == set(by_namespace.methods())


def test_an_alias_that_is_not_snake_case_is_made_fit_for_the_namespace() -> None:
    gateway = RpcGateway([_billing("sincpro-billing")], unguarded=True)

    assert list(gateway.catalogs) == ["sincpro-billing"]
    assert set(gateway.methods()) == {
        "sincpro_billing.issue_invoice",
        "sincpro_billing.invoice",
    }


def test_a_declared_name_is_the_whole_name() -> None:
    gateway = RpcGateway(
        {"billing": _billing(issue=rpc("billing.invoices.issue"))}, unguarded=True
    )

    assert set(gateway.methods()) == {"billing.invoices.issue", "billing.invoice"}
    assert [entry.name for entry in gateway.manifest()] == [
        "billing.invoice",
        "billing.invoices.issue",
    ]


@pytest.mark.parametrize(
    ("declared", "rule"),
    [
        ("issue", "namespace.operation"),
        ("Billing.Issue", "namespace.operation"),
        ("billing.issue-invoice", "namespace.operation"),
        ("rpc.issue", "reserved"),
    ],
)
def test_a_declared_name_that_breaks_the_rule_is_refused_at_build(
    declared: str, rule: str
) -> None:
    gateway = RpcGateway({"billing": _billing(issue=rpc(declared))}, unguarded=True)

    assert rule in _refused(gateway)


def test_an_override_name_is_validated_too() -> None:
    gateway = RpcGateway({"billing": _billing()}, unguarded=True)
    gateway.override(CommandIssueInvoice, name="Issue")

    assert "namespace.operation" in _refused(gateway)


def test_a_group_namespace_under_rpc_is_refused() -> None:
    bus = _billing()
    gateway = RpcGateway({"billing": bus}, unguarded=True).group(bus, namespace="rpc")

    assert "reserved" in _refused(gateway)


def test_two_operations_answering_one_name_fail_the_build() -> None:
    """`CommandInvoice` and `QueryInvoice` both derive `billing.invoice`."""

    class CommandInvoice(DataTransferObject):
        pass

    bus = _billing(build=False)

    @bus.feature(CommandInvoice)
    @rpc()
    class WriteInvoice(Feature):
        def execute(self, dto: CommandInvoice) -> None:
            return None

    refused = _refused(RpcGateway({"billing": bus}, unguarded=True))

    assert "billing.invoice" in refused
    assert "CommandInvoice" in refused and "QueryInvoice" in refused


def test_two_buses_under_one_namespace_clash() -> None:
    first, second = _billing("first"), _billing("second")
    gateway = RpcGateway({"a": first, "b": second}, unguarded=True)
    gateway.group(second, namespace="a")

    assert "a.issue_invoice" in _refused(gateway)


# --- declared exposure ----------------------------------------------------------------------


def test_declared_is_the_default_and_publishes_only_what_is_bound() -> None:
    bus = _billing(read=lambda cls: cls)
    gateway = RpcGateway({"billing": bus}, unguarded=True)

    assert gateway.exposure is Exposure.DECLARED
    assert set(gateway.methods()) == {"billing.issue_invoice"}
    unbound = gateway.handle(_call("billing.reconcile"))
    assert isinstance(unbound, dict) and unbound["error"]["code"] == METHOD_NOT_FOUND
    documented = {one["name"] for one in gateway.discover()["methods"]}
    assert documented == {"rpc.discover", "billing.issue_invoice"}


def test_bind_publishes_a_use_case_no_decorator_bound() -> None:
    gateway = RpcGateway({"billing": _billing()}, unguarded=True)
    gateway.bind(CommandReconcile, RpcBinding(name="billing.ledger.reconcile"))

    assert "billing.ledger.reconcile" in gateway.methods()
    assert gateway.handle(_call("billing.ledger.reconcile")) == {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {},
    }


def test_catalog_publishes_every_use_case_under_the_same_names() -> None:
    gateway = RpcGateway({"billing": _billing()}, exposure=Exposure.CATALOG, unguarded=True)

    assert set(gateway.methods()) == {
        "billing.issue_invoice",
        "billing.invoice",
        "billing.reconcile",
    }


def test_a_bus_with_no_access_control_is_refused_unless_said_on_purpose() -> None:
    refused = _refused(RpcGateway({"billing": _billing()}))

    assert "unguarded=True" in refused


def test_the_manifest_names_what_a_client_calls() -> None:
    manifest = RpcGateway({"billing": _billing()}, unguarded=True).manifest()

    assert [(one.wire, one.context, one.name) for one in manifest] == [
        ("rpc", "billing", "billing.invoice"),
        ("rpc", "billing", "billing.issue_invoice"),
    ]


def test_a_port_that_is_not_a_json_rpc_wire_is_refused() -> None:
    class Elsewhere(Wire[RpcBinding]):
        binding = RpcBinding

        def derive(self, operation: Any, group: Any) -> RpcBinding:
            return RpcBinding()

        def validate(self, surface: Any) -> list[str]:
            return []

        def build(self, surface: Any) -> Any:
            return surface

    with pytest.raises(TypeError, match="JsonRpcWire"):
        RpcGateway({"billing": _billing()}, unguarded=True, port=Elsewhere())


def test_a_port_of_the_projects_derives_the_names() -> None:
    class Upper(JsonRpcWire):
        def operation_of(self, dto_name: str) -> str:
            return super().operation_of(dto_name) + "_v1"

    port = Upper(title="billing-api", max_batch_size=3)
    gateway = RpcGateway({"billing": _billing()}, unguarded=True, port=port)

    assert set(gateway.methods()) == {"billing.issue_invoice_v1", "billing.invoice_v1"}
    assert gateway.discover()["info"]["title"] == "billing-api"
    assert gateway.max_batch_size == 3
    with pytest.raises(ValueError, match="port"):
        RpcGateway({"billing": _billing()}, unguarded=True, port=port, max_batch_size=9)


# --- the document ---------------------------------------------------------------------------


class Perm(Permission):
    ISSUE = "billing.invoice.issue"


def _guarded() -> UseFramework:
    auth = AccessControl[Perm](
        providers=[
            StaticProvider({"t-issuer": Identity.user("user:1", permissions={Perm.ISSUE})})
        ]
    )
    idempotency = Idempotency(InMemoryKeyValue())
    once = idempotency.once(expires_after=timedelta(minutes=5))
    bus = UseFramework("guarded-billing", log_after_execution=False)

    @bus.feature(CommandIssueInvoice)
    @once
    @auth.requires(Perm.ISSUE)
    @rpc()
    class IssueInvoice(Feature):
        def execute(self, dto: CommandIssueInvoice) -> Issued:
            return Issued(number=f"F-{dto.customer_id}")

    @bus.feature(QueryInvoice)
    @auth.public
    @rpc()
    class GetInvoice(Feature):
        def execute(self, dto: QueryInvoice) -> Issued:
            return Issued(number=dto.invoice_id)

    auth.on(bus)
    bus.build_root_bus()
    return bus


def test_the_document_tags_each_method_by_context_and_says_access_and_idempotency() -> None:
    document = RpcGateway({"billing": _guarded()}).discover()
    methods = {one["name"]: one for one in document["methods"]}

    issue, read = methods["billing.issue_invoice"], methods["billing.invoice"]
    assert issue["tags"] == [{"name": "billing"}]
    assert issue["x-sincpro-requires"] == "billing.invoice.issue"
    assert read["x-sincpro-requires"] == "public"
    assert (issue["x-idempotent"], read["x-idempotent"]) == (True, True)
    assert "x-sincpro-layer" not in issue


def test_rpc_discover_answers_the_same_document_as_the_route() -> None:
    gateway = RpcGateway({"billing": _billing()}, unguarded=True)
    discovered = gateway.handle({"jsonrpc": "2.0", "id": 1, "method": "rpc.discover"})

    assert isinstance(discovered, dict)
    assert discovered["result"] == TestClient(gateway.app()).get("/openrpc.json").json()


# --- full control: dispatch -----------------------------------------------------------------


def _own_app(gateway: RpcGateway) -> Starlette:
    """A route of the project's own — what PRD_15 §4 calls full control."""
    surface = gateway.build()

    async def endpoint(request: Request) -> Response:
        if not is_json_media_type(request.headers.get("content-type")):
            return JSONResponse({}, status_code=415)
        reply = dispatch(
            surface,
            await request.body(),
            credentials=credentials_from_asgi(request.scope),
            headers=request.headers,
        )
        status, headers = http_status(surface, reply)
        if reply is None:
            return Response(status_code=status, headers=headers)
        return JSONResponse(reply, status_code=status, headers=headers)

    return Starlette(routes=[Route("/rpc", endpoint, methods=["POST"])])


def _parity_bus() -> UseFramework:
    auth = AccessControl[Perm](
        providers=[
            StaticProvider({"t-issuer": Identity.user("user:1", permissions={Perm.ISSUE})})
        ]
    )
    bus = UseFramework("parity-billing", log_after_execution=False)

    @bus.feature(CommandIssueInvoice)
    @auth.requires(Perm.ISSUE)
    @rpc()
    class IssueInvoice(Feature):
        def execute(self, dto: CommandIssueInvoice) -> Issued:
            return Issued(number=f"F-{dto.customer_id}")

    @bus.feature(QueryInvoice)
    @auth.public
    @rpc()
    class GetInvoice(Feature):
        def execute(self, dto: QueryInvoice) -> Issued:
            return Issued(number=dto.invoice_id)

    auth.on(bus)
    bus.build_root_bus()
    return bus


PARITY_CASES: list[tuple[str, Any, dict[str, str]]] = [
    ("call", _call("billing.invoice", {"invoice_id": "F-1"}), {}),
    ("unauthenticated", _call("billing.issue_invoice", {"customer_id": 1}), {}),
    (
        "authenticated",
        _call("billing.issue_invoice", {"customer_id": 1}),
        {"authorization": "Bearer t-issuer"},
    ),
    ("notification", {"jsonrpc": "2.0", "method": "billing.invoice"}, {}),
    ("unknown", _call("billing.features.QueryInvoice"), {}),
    ("batch-over-limit", [_call("billing.invoice", {"invoice_id": "x"})] * 3, {}),
    (
        "batch",
        [_call("billing.invoice", {"invoice_id": "a"}, 1), _call("billing.nope", None, 2)],
        {},
    ),
    ("invalid-json", b'{"jsonrpc": "2.0", "method"', {}),
    ("body-too-large", {**_call("billing.invoice"), "context": {"pad": "x" * 600}}, {}),
    ("discover", {"jsonrpc": "2.0", "id": 9, "method": "rpc.discover"}, {}),
]


@pytest.mark.parametrize(
    ("payload", "headers"),
    [(payload, headers) for _, payload, headers in PARITY_CASES],
    ids=[name for name, _, _ in PARITY_CASES],
)
def test_dispatch_answers_exactly_what_the_gateway_app_answers(
    payload: Any, headers: dict[str, str]
) -> None:
    gateway = RpcGateway({"billing": _parity_bus()}, max_batch_size=2, max_body_bytes=512)
    generated, own = TestClient(gateway.app()), TestClient(_own_app(gateway))
    content = payload if isinstance(payload, bytes) else None
    sent = {"content-type": "application/json", **headers}

    def post(client: TestClient) -> Any:
        if content is not None:
            return client.post("/rpc", content=content, headers=sent)
        return client.post("/rpc", json=payload, headers=sent)

    a, b = post(generated), post(own)

    assert (a.status_code, a.content) == (b.status_code, b.content)
    assert a.headers.get("www-authenticate") == b.headers.get("www-authenticate")


def test_dispatch_takes_the_gateway_surface_as_it_is() -> None:
    gateway = RpcGateway({"billing": _billing()}, unguarded=True)

    answered = dispatch(
        gateway.surface(),
        _call("billing.invoice", {"invoice_id": "F-2"}),
        headers={"x-correlation-id": "c-1"},
    )

    assert answered == {"jsonrpc": "2.0", "id": 1, "result": {"number": "F-2"}}
    assert isinstance(gateway.build(), RpcSurface)
    assert http_status(gateway.build(), None) == (204, {})


@pytest.mark.parametrize(
    ("payload", "status"),
    [
        (_call("billing.issue_invoice", {"customer_id": 1}), 401),
        ({"jsonrpc": "2.0", "method": "billing.invoice"}, 204),
        (b"{", 200),
        ({**_call("billing.invoice"), "context": {"pad": "x" * 600}}, 413),
        ([_call("billing.issue_invoice", {"customer_id": 1})], 200),
    ],
    ids=["lone-unauthenticated", "notification", "parse-error", "too-large", "batch"],
)
def test_http_status_follows_prd_15(payload: Any, status: int) -> None:
    """A JSON-RPC error is a 200 — except a lone unauthenticated request (401 + where to
    authenticate), a body over the limit (413) and nothing to answer (204)."""
    surface = RpcGateway({"billing": _parity_bus()}, max_body_bytes=512).build()
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    reply = dispatch(surface, body)

    assert http_status(surface, reply)[0] == status
