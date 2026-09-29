"""Every entrypoint authenticates by itself when the bus is guarded — JSON-RPC, gRPC, MCP, an ASGI
app of the project's and a call to another service — with the same providers, and refuses the
same way each protocol's clients already understand.

The incidents: a guarded use case reached through a wire that never asked who was calling; a
refusal answered with a code no client branches on; an identity claimed in a request context
believed; a bus nobody guards suddenly refusing.
"""

import asyncio
import json
from collections.abc import Iterator, Mapping
from typing import Any

import grpc
import pytest
from google.protobuf.struct_pb2 import Struct
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.auth import (
    AccessControl,
    ApiKey,
    ApiKeyProvider,
    AuthProvider,
    Credentials,
    Identity,
    IdentityKind,
    IdentityMiddleware,
    InMemoryApiKeys,
    Permission,
    PermissionDenied,
    ServiceTokenProvider,
    StaticProvider,
    Unauthenticated,
    as_identity,
    as_system,
    credentials_from_asgi,
    current_identity,
)
from sincpro_framework.auth.adapters.service_token_provider import HEADER
from sincpro_framework.auth.transports import identity_headers
from sincpro_framework.entrypoints.exposure import Exposure, GrpcBinding, RpcBinding
from sincpro_framework.entrypoints.grpc import GrpcGateway
from sincpro_framework.entrypoints.grpc.wire import scalar_to_struct, struct_to_scalar
from sincpro_framework.entrypoints.mcp import auth as mcp_auth
from sincpro_framework.entrypoints.mcp.mcp import fastmcp_callable
from sincpro_framework.entrypoints.rpc import RpcGateway
from sincpro_framework.entrypoints.rpc.jrpc import PERMISSION_DENIED, UNAUTHENTICATED
from sincpro_framework.remote_execution.domain.payload import ChunkReader, packed, unpacked
from sincpro_framework.remote_execution.entrypoint.execution import execute_hosted
from sincpro_framework.testing import AuthProviderContract


class Perm(Permission):
    ISSUE = "billing.invoice.issue"
    AUDIT = "billing.audit"


class CommandIssue(DataTransferObject):
    total: int = 1


class CommandHealth(DataTransferObject):
    pass


class Issued(DataTransferObject):
    by: str


class _Challenging(StaticProvider):
    def challenge(self) -> str | None:
        return 'Bearer realm="billing"'


ISSUER = Identity.user("user:1", tenant="bo", permissions={Perm.ISSUE})
CLERK = Identity.user("user:2", tenant="bo")


def _guarded(name: str = "wired-billing") -> tuple[UseFramework, AccessControl[Perm]]:
    auth = AccessControl[Perm](
        providers=[_Challenging({"t-issuer": ISSUER, "t-clerk": CLERK})]
    )
    billing = UseFramework(name, log_after_execution=False)

    @billing.feature(CommandIssue)
    @auth.requires(Perm.ISSUE)
    class Issue(Feature):
        def execute(self, dto: CommandIssue) -> Issued:
            return Issued(by=current_identity().subject)

    @billing.feature(CommandHealth)
    @auth.public
    class Health(Feature):
        def execute(self, dto: CommandHealth) -> Issued:
            return Issued(by=current_identity().subject)

    auth.on(billing)
    return billing, auth


def _bearer(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


# JSON-RPC


@pytest.fixture
def rpc() -> TestClient:
    billing, _ = _guarded()
    gateway = RpcGateway({"billing": billing})
    gateway.bind(CommandIssue, RpcBinding()).bind(CommandHealth, RpcBinding())
    return TestClient(gateway.app())


def _call(method: str, request_id: int = 1) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": {}}


ISSUE_METHOD = "billing.issue"
HEALTH_METHOD = "billing.health"


def test_rpc_asks_nobody_to_authenticate_with_401_and_where(rpc: TestClient) -> None:
    answer = rpc.post("/rpc", json=_call(ISSUE_METHOD))
    assert answer.status_code == 401
    assert answer.headers["www-authenticate"] == 'Bearer realm="billing"'
    assert answer.json()["error"]["code"] == UNAUTHENTICATED


def test_rpc_runs_as_the_token_and_refuses_what_it_lacks(rpc: TestClient) -> None:
    ran = rpc.post("/rpc", json=_call(ISSUE_METHOD), headers=_bearer("t-issuer"))
    assert ran.json()["result"] == {"by": "user:1"}
    refused = rpc.post("/rpc", json=_call(ISSUE_METHOD), headers=_bearer("t-clerk"))
    assert refused.status_code == 200
    error = refused.json()["error"]
    assert error["code"] == PERMISSION_DENIED
    assert error["data"]["requirement"] == Perm.ISSUE


def test_rpc_answers_a_batch_item_by_item(rpc: TestClient) -> None:
    replies = rpc.post("/rpc", json=[_call(ISSUE_METHOD, 1), _call(HEALTH_METHOD, 2)]).json()
    by_id = {one["id"]: one for one in replies}
    assert by_id[1]["error"]["code"] == UNAUTHENTICATED
    assert by_id[2]["result"] == {"by": "anonymous"}


def test_rpc_refuses_a_forged_token_and_never_believes_the_body_context(
    rpc: TestClient,
) -> None:
    forged = rpc.post("/rpc", json=_call(ISSUE_METHOD), headers=_bearer("stolen"))
    assert forged.status_code == 401
    claimed = {**_call(ISSUE_METHOD), "context": {"identity": ISSUER.model_dump(mode="json")}}
    assert rpc.post("/rpc", json=claimed).status_code == 401


def test_a_bus_nobody_guards_answers_as_before() -> None:
    plain = UseFramework("plain-billing", log_after_execution=False)

    @plain.feature(CommandIssue)
    class Issue(Feature):
        def execute(self, dto: CommandIssue) -> Issued:
            return Issued(by=current_identity().subject)

    gateway = RpcGateway({"billing": plain}, exposure=Exposure.CATALOG, unguarded=True)
    client = TestClient(gateway.app())
    assert client.post("/rpc", json=_call(ISSUE_METHOD)).json()["result"] == {
        "by": "anonymous"
    }


# gRPC


@pytest.fixture
def grpc_channel() -> Iterator[grpc.Channel]:
    billing, _ = _guarded("grpc-billing")
    gateway = GrpcGateway({"billing": billing}).bind(CommandIssue, GrpcBinding())
    server = gateway.server(max_workers=2)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    yield channel
    channel.close()
    server.stop(None)


def _grpc_call(channel: grpc.Channel, token: str | None) -> dict[str, Any]:
    call = channel.unary_unary(
        "/billing.v1.BillingService/Issue",
        request_serializer=Struct.SerializeToString,
        response_deserializer=Struct.FromString,
    )
    metadata = (("authorization", f"Bearer {token}"),) if token else None
    return struct_to_scalar(call(scalar_to_struct({}), metadata=metadata))


def test_grpc_answers_the_codes_its_clients_branch_on(grpc_channel: grpc.Channel) -> None:
    assert _grpc_call(grpc_channel, "t-issuer") == {"by": "user:1"}
    for token, code in (
        (None, grpc.StatusCode.UNAUTHENTICATED),
        ("t-clerk", grpc.StatusCode.PERMISSION_DENIED),
    ):
        with pytest.raises(grpc.RpcError) as refused:
            _grpc_call(grpc_channel, token)
        assert refused.value.code() == code  # pyright: ignore[reportAttributeAccessIssue]
    with pytest.raises(grpc.RpcError) as nobody:
        _grpc_call(grpc_channel, None)
    refusal: Any = nobody.value
    trailing = {str(key): str(value) for key, value in refusal.trailing_metadata()}
    assert trailing["www-authenticate"] == 'Bearer realm="billing"'
    assert json.loads(trailing["sp-auth-refusal"])["kind"] == "unauthenticated"


# MCP — FastMCP is an optional extra the CI does not install: these skip without it


class _Request:
    def __init__(self, headers: Mapping[str, str]) -> None:
        self.scope = {
            "type": "http",
            "method": "POST",
            "path": "/mcp",
            "query_string": b"",
            "headers": [(name.encode(), value.encode()) for name, value in headers.items()],
        }


def _as_mcp_http_call(monkeypatch: pytest.MonkeyPatch, headers: Mapping[str, str]) -> None:
    from fastmcp.server import dependencies  # pyright: ignore[reportMissingImports]

    monkeypatch.setattr(dependencies, "get_access_token", lambda: None)
    monkeypatch.setattr(dependencies, "get_http_request", lambda: _Request(headers))


def _mcp_issue(billing: UseFramework) -> Any:
    from sincpro_framework.entrypoints.catalog import Catalog

    operation = next(
        one for one in Catalog(billing).get_scalar_use_cases() if one.name == "CommandIssue"
    )
    return fastmcp_callable(operation, billing)


def test_an_mcp_tool_call_acts_as_whoever_the_request_says(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("fastmcp")
    billing, _ = _guarded("mcp-billing")
    issue = _mcp_issue(billing)
    _as_mcp_http_call(monkeypatch, _bearer("t-issuer"))
    assert issue() == {"by": "user:1"}
    _as_mcp_http_call(monkeypatch, _bearer("t-clerk"))
    with pytest.raises(PermissionDenied):
        issue()


def test_fastmcp_verifies_bearers_with_the_buses_providers() -> None:
    pytest.importorskip("fastmcp")
    _, auth = _guarded("verified-billing")
    verifier = mcp_auth.token_verifier(auth)
    verified = asyncio.run(verifier.verify_token("t-issuer"))
    assert verified is not None and verified.subject == "user:1"
    assert asyncio.run(verifier.verify_token("stolen")) is None
    assert asyncio.run(verifier.verify_token("")) is None


def test_an_mcp_tool_call_reads_the_identity_the_verifier_put_in_the_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("fastmcp")
    from fastmcp.server import dependencies  # pyright: ignore[reportMissingImports]

    billing, auth = _guarded("token-billing")
    verified = asyncio.run(mcp_auth.token_verifier(auth).verify_token("t-issuer"))
    monkeypatch.setattr(dependencies, "get_access_token", lambda: verified)
    assert _mcp_issue(billing)() == {"by": "user:1"}


# An ASGI app of the project's — FastAPI, Starlette


def _project_app(billing: UseFramework, auth: AccessControl[Perm]) -> TestClient:
    async def issue(request: Request) -> JSONResponse:
        answer = await asyncio.to_thread(billing, CommandIssue(), Issued)
        assert answer is not None
        return JSONResponse({"by": answer.by})

    app = Starlette(routes=[Route("/invoices", issue, methods=["POST"])])
    app.add_middleware(IdentityMiddleware, access=auth)
    return TestClient(app)


def test_a_project_app_acts_as_the_request_and_answers_refusals_by_status() -> None:
    billing, auth = _guarded("asgi-billing")
    client = _project_app(billing, auth)
    assert client.post("/invoices", headers=_bearer("t-issuer")).json() == {"by": "user:1"}
    nobody = client.post("/invoices")
    assert nobody.status_code == 401
    assert nobody.headers["www-authenticate"] == 'Bearer realm="billing"'
    denied = client.post("/invoices", headers=_bearer("t-clerk"))
    assert (denied.status_code, denied.json()["error"]["requirement"]) == (403, Perm.ISSUE)
    assert client.post("/invoices", headers=_bearer("stolen")).status_code == 401


# A call to another service


def test_the_identity_crosses_to_another_service_signed_and_nothing_else_does() -> None:
    keys = {"2026-09": "a-secret-both-services-share"}
    caller_auth = AccessControl[Perm](
        providers=[StaticProvider({"t-issuer": ISSUER}), ServiceTokenProvider("orders", keys)]
    )
    caller = UseFramework("orders", log_after_execution=False)
    caller_auth.on(caller)
    host, host_auth = _guarded("hosted-billing")
    host_auth.providers = (*host_auth.providers, ServiceTokenProvider("billing", keys))

    def hosted(headers: Mapping[str, str]) -> Issued:
        credentials = Credentials(transport="service", headers=headers)
        name = next(one for one in host.dto_registry if one.endswith(".CommandIssue"))
        body = ChunkReader(iter(packed(CommandIssue())))
        answer = execute_hosted(
            {host.name: host}, host.name, name, body, None, {}, credentials
        )
        return unpacked(ChunkReader(answer), Issued)

    with as_identity(
        caller_auth.authenticate(Credentials(transport="http", headers=_bearer("t-issuer")))
    ):
        sent = identity_headers("hosted-billing")
    assert set(sent) == {HEADER}
    assert hosted(sent).by == "user:1"
    with pytest.raises(Unauthenticated):
        hosted({})
    with as_system("cron"):
        assert identity_headers("hosted-billing") == {}


# The providers shipped


def _keys() -> tuple[InMemoryApiKeys, str]:
    store = InMemoryApiKeys()
    secret = store.issue(
        ApiKey(subject="apikey:ops-bot", tenant="bo", permissions=frozenset({Perm.AUDIT}))
    )
    return store, secret


def test_an_api_key_is_kept_by_its_digest_and_read_from_where_clients_send_it() -> None:
    store, secret = _keys()
    provider = ApiKeyProvider(store, query="access_token")
    assert secret not in json.dumps(
        [one.model_dump(mode="json") for one in store._by_digest.values()]
    )
    for credentials in (
        Credentials(transport="http", headers={"x-api-key": secret}),
        Credentials(transport="http", headers={"authorization": f"ApiKey {secret}"}),
        Credentials(transport="http", query={"access_token": secret}),
    ):
        identity = provider.authenticate(credentials)
        assert identity is not None and identity.kind == IdentityKind.SERVICE
    store.revoke(secret)
    with pytest.raises(Unauthenticated, match="revoked"):
        provider.authenticate(Credentials(transport="http", headers={"x-api-key": secret}))


def test_a_service_token_is_refused_tampered_expired_or_for_another_audience() -> None:
    keys = {"2026-09": "shared"}
    issuer = ServiceTokenProvider("orders", keys)
    token = issuer.token_for(ISSUER)
    head, claims, signature = token.split(".")
    for bad in (
        f"{head}.{claims}.{signature[:-2]}xx",
        ServiceTokenProvider("orders", keys, ttl_seconds=-1).token_for(ISSUER),
        ServiceTokenProvider("orders", keys, audience="elsewhere").token_for(ISSUER),
        ServiceTokenProvider("orders", {"old": "other"}).token_for(ISSUER),
        "not-a-token",
    ):
        with pytest.raises(Unauthenticated):
            issuer.authenticate(Credentials(transport="service", headers={HEADER: bad}))
    rotated = ServiceTokenProvider("orders", {"2026-08": "older", **keys})
    assert rotated.authenticate(
        Credentials(transport="service", headers={HEADER: issuer.token_for(ISSUER)})
    )


def test_a_service_token_carries_who_acts_and_never_the_claims() -> None:
    issuer = ServiceTokenProvider("orders", {"k": "shared"})
    secretive = ISSUER.model_copy(update={"claims": {"tenant_odoo_token": "do-not-leak"}})
    token = issuer.token_for(secretive)
    assert "do-not-leak" not in token and "do-not-leak" not in json.dumps(
        [
            json.loads(__import__("base64").urlsafe_b64decode(part + "=="))
            for part in token.split(".")[:2]
        ]
    )
    identity = issuer.authenticate(Credentials(transport="service", headers={HEADER: token}))
    assert identity is not None and identity.actor is not None
    assert (identity.subject, identity.actor.subject) == ("user:1", "service:orders")


def test_credentials_are_read_whole_from_an_asgi_scope() -> None:
    credentials = credentials_from_asgi(
        {
            "type": "http",
            "method": "POST",
            "path": "/rpc",
            "query_string": b"access_token=q-1&x=2",
            "headers": [(b"Authorization", b"Bearer b-1"), (b"cookie", b"session_id=s-1")],
        }
    )
    assert credentials.bearer == "b-1"
    assert credentials.cookies == {"session_id": "s-1"}
    assert credentials.query["access_token"] == "q-1"
    assert (credentials.method, credentials.uri) == ("POST", "/rpc?access_token=q-1&x=2")


class TestApiKeyProvider(AuthProviderContract):
    def make_provider(self) -> AuthProvider:
        self.store, self.secret = _keys()
        return ApiKeyProvider(self.store)

    def accepted(self) -> Credentials:
        return Credentials(transport="http", headers={"x-api-key": self.secret})

    def foreign(self) -> Credentials:
        return Credentials(transport="http", headers={"authorization": "Bearer t-issuer"})

    def rejected(self) -> Credentials:
        return Credentials(transport="http", headers={"x-api-key": "sk_forged"})

    def granted_permission(self) -> str | None:
        return Perm.AUDIT


class TestServiceTokenProvider(AuthProviderContract):
    provider = ServiceTokenProvider("orders", {"k": "shared"})

    def make_provider(self) -> AuthProvider:
        return self.provider

    def accepted(self) -> Credentials:
        return Credentials(
            transport="service", headers={HEADER: self.provider.token_for(ISSUER)}
        )

    def foreign(self) -> Credentials:
        return Credentials(transport="http", headers={"x-api-key": "k"})

    def rejected(self) -> Credentials:
        return Credentials(transport="service", headers={HEADER: "a.b.c"})

    def granted_permission(self) -> str | None:
        return Perm.ISSUE
