"""The primitives a host composes its own process around: routes, handlers,
lifespan, middleware, per-bus policy. No entrypoint here picks an auth
mechanism or a CORS policy for the host — it hands back what FastAPI hands
back for `app.routes`: data the host arranges itself.
"""

from collections.abc import Iterator
from contextlib import asynccontextmanager

import grpc
import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.entrypoints.grpc import GrpcGateway
from sincpro_framework.entrypoints.grpc.client import GrpcClient
from sincpro_framework.entrypoints.grpc.wire import Struct, scalar_to_struct, struct_to_scalar
from sincpro_framework.entrypoints.rpc import RpcGateway


class Ask(DataTransferObject):
    n: int


class Response(DataTransferObject):
    total: int


def _bus(name: str) -> UseFramework:
    framework = UseFramework(name, log_after_execution=False)

    @framework.feature(Ask)
    class DoIt(Feature):
        def execute(self, dto: Ask) -> Response:
            return Response(total=dto.n)

    framework.build_root_bus()
    return framework


# --- entrypoint_rpc: routes() as data, app() as composition -----------------


def test_routes_returns_starlette_route_objects_not_an_app():
    routes = RpcGateway({"pay": _bus("pay")}).routes()

    assert {route.path for route in routes} == {"/rpc", "/openrpc.json", "/healthz"}
    assert all(isinstance(route, Route) for route in routes)


def test_routes_health_path_none_drops_the_health_route():
    routes = RpcGateway({"pay": _bus("pay")}).routes(health_path=None)

    assert {route.path for route in routes} == {"/rpc", "/openrpc.json"}


def test_routes_paths_are_configurable_for_mounting_under_a_prefix():
    routes = RpcGateway({"pay": _bus("pay")}).routes(
        rpc_path="/payments/rpc",
        discover_path="/payments/openrpc.json",
        health_path="/payments/healthz",
    )

    assert {route.path for route in routes} == {
        "/payments/rpc",
        "/payments/openrpc.json",
        "/payments/healthz",
    }


def test_host_composes_its_own_starlette_around_routes():
    """The host owns the app: its own health route, its own CORS policy — the
    gateway supplies none of either.
    """

    async def healthz(_request: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    gateway = RpcGateway({"pay": _bus("pay")})
    app = Starlette(
        routes=[Route("/healthz", healthz), *gateway.routes()],
        middleware=[Middleware(CORSMiddleware, allow_origins=["https://app.example.com"])],
    )
    client = TestClient(app)

    assert client.get("/healthz").text == "ok"
    preflight = client.options(
        "/rpc",
        headers={
            "Origin": "https://app.example.com",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert preflight.headers["access-control-allow-origin"] == "https://app.example.com"
    denied = client.options(
        "/rpc",
        headers={
            "Origin": "https://evil.example.com",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert "access-control-allow-origin" not in denied.headers


def test_app_accepts_middleware_routes_and_lifespan_without_the_host():
    started: list[str] = []

    @asynccontextmanager
    async def lifespan(_app: Starlette):
        started.append("up")
        yield
        started.append("down")

    async def healthz(_request: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    gateway = RpcGateway({"pay": _bus("pay")})
    app = gateway.app(
        middleware=[Middleware(CORSMiddleware, allow_origins=["https://app.example.com"])],
        routes=[Route("/healthz-custom", healthz)],
        lifespan=lifespan,
    )

    with TestClient(app) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.get("/healthz-custom").text == "ok"
        reply = client.post(
            "/rpc",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "pay.features.Ask",
                "params": {"n": 3},
            },
        )
        assert reply.json()["result"] == {"total": 3}
    assert started == ["up", "down"]


def test_app_with_no_arguments_still_works_exactly_as_before():
    gateway = RpcGateway({"pay": _bus("pay")})
    client = TestClient(gateway.app())

    reply = client.post(
        "/rpc",
        json={"jsonrpc": "2.0", "id": 1, "method": "pay.features.Ask", "params": {"n": 1}},
    )
    assert reply.json()["result"] == {"total": 1}


# --- entrypoint_grpc: handlers() as data, per-bus policy via interceptor ---


def test_handlers_returns_generic_rpc_handlers_with_no_server():
    handlers = GrpcGateway({"pay": _bus("pay")}).handlers()

    # pay.Features + sincpro.Introspection + grpc.health.v1.Health (on by default)
    assert len(handlers) == 3
    assert all(hasattr(handler, "service_name") for handler in handlers)


class _DenyInternalWithoutToken(grpc.ServerInterceptor):
    """A per-bus policy written entirely outside this package: the alias comes
    from the method path this gateway already publishes.
    """

    def intercept_service(self, continuation, handler_call_details):
        method = handler_call_details.method
        alias = method.lstrip("/").split(".", 1)[0]
        if alias == "internal":
            metadata = dict(handler_call_details.invocation_metadata or ())
            if metadata.get("x-internal-token") != "s3cr3t":

                def deny(_request, context):
                    context.abort(grpc.StatusCode.PERMISSION_DENIED, f"bus [{alias}] locked")

                return grpc.unary_unary_rpc_method_handler(deny)
        return continuation(handler_call_details)


@pytest.fixture
def per_bus_policy_client() -> Iterator[GrpcClient]:
    gateway = GrpcGateway({"public": _bus("public"), "internal": _bus("internal")})
    server = gateway.server(interceptors=[_DenyInternalWithoutToken()], reflection=False)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    client = GrpcClient(f"127.0.0.1:{port}")
    try:
        yield client
    finally:
        client.close()
        server.stop(None)


def test_interceptor_applies_a_different_policy_per_alias(per_bus_policy_client: GrpcClient):
    assert per_bus_policy_client.call("/public.Features/Ask", {"n": 1}) == {"total": 1.0}

    with pytest.raises(grpc.RpcError) as denied:
        per_bus_policy_client.call("/internal.Features/Ask", {"n": 1})
    assert denied.value.code() is grpc.StatusCode.PERMISSION_DENIED

    allowed = per_bus_policy_client.channel.unary_unary(
        "/internal.Features/Ask",
        request_serializer=Struct.SerializeToString,
        response_deserializer=Struct.FromString,
    )(scalar_to_struct({"n": 1}), metadata=(("x-internal-token", "s3cr3t"),))
    assert struct_to_scalar(allowed) == {"total": 1.0}


def test_a_caller_builds_its_own_grpc_server_from_handlers():
    """No `.server()`, no `.run()` — the host owns the `grpc.Server` entirely,
    handlers() only supplies what to mount on it.
    """
    from concurrent import futures

    gateway = GrpcGateway({"pay": _bus("pay")})
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    server.add_generic_rpc_handlers(gateway.handlers())
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()

    with GrpcClient(f"127.0.0.1:{port}") as client:
        assert client.call("/pay.Features/Ask", {"n": 5}) == {"total": 5.0}
    server.stop(None)
