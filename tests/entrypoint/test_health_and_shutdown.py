"""grpc.health.v1.Health served by default, and SIGTERM/SIGINT draining `run()`.

Neither is opt-in: a gateway that reaches `.server()`/`.run()` already forced its
buses to build (`Catalog.__init__` calls `build_root_bus()`), so shipping without a
way for k8s/grpcurl to ask "are you up" — or without draining a rolling restart —
is the wrong default, not the safe one. Both stay overridable: `health_check=` for a
deeper probe, `handle_signals=False` for a caller that hooks its own lifecycle.
"""

import signal
import time
from collections.abc import Iterator
from concurrent import futures
from typing import Any

import grpc
import pytest
from grpc_health.v1 import health_pb2  # pyright: ignore[reportMissingImports]
from grpc_health.v1 import (  # pyright: ignore[reportMissingImports]
    health_pb2_grpc,
)

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.entrypoints.grpc import GrpcGateway
from sincpro_framework.entrypoints.grpc.client import GrpcClient
from sincpro_framework.entrypoints.grpc.entrypoint import SHUTDOWN_SIGNALS
from sincpro_framework.entrypoints.grpc.wire import HEALTH_SERVICE


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


@pytest.fixture
def served() -> Iterator[tuple[grpc.Channel, Any]]:
    gateway = GrpcGateway({"payments": _bus("payments")})
    server = gateway.server(reflection=False)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    try:
        yield channel, health_pb2_grpc.HealthStub(channel)
    finally:
        channel.close()
        server.stop(None)


# --- grpc.health.v1.Health is served by default -----------------------------


def test_health_service_appears_in_handlers_by_default():
    handlers = GrpcGateway({"payments": _bus("payments")}).handlers()

    assert HEALTH_SERVICE in {h.service_name() for h in handlers}


def test_check_overall_and_per_service_are_serving(served):
    _channel, stub = served

    overall = stub.Check(health_pb2.HealthCheckRequest(service=""))
    per_service = stub.Check(health_pb2.HealthCheckRequest(service="payments.Features"))

    assert overall.status == health_pb2.HealthCheckResponse.SERVING
    assert per_service.status == health_pb2.HealthCheckResponse.SERVING


def test_check_unknown_service_is_not_found(served):
    _channel, stub = served

    with pytest.raises(grpc.RpcError) as error:
        stub.Check(health_pb2.HealthCheckRequest(service="no.existe"))

    assert error.value.code() is grpc.StatusCode.NOT_FOUND


def test_watch_streams_the_current_status(served):
    _channel, stub = served

    first = next(iter(stub.Watch(health_pb2.HealthCheckRequest(service=""))))

    assert first.status == health_pb2.HealthCheckResponse.SERVING


def test_a_custom_health_check_overrides_the_default():
    """A caller wiring a deeper probe (a DB ping) replaces "is the bus built"."""
    ready = {"value": False}
    gateway = GrpcGateway({"payments": _bus("payments")})
    server = gateway.server(reflection=False, health_check=lambda: ready["value"])
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    stub: Any = health_pb2_grpc.HealthStub(channel)

    not_ready = stub.Check(health_pb2.HealthCheckRequest(service=""))
    ready["value"] = True
    now_ready = stub.Check(health_pb2.HealthCheckRequest(service=""))

    assert not_ready.status == health_pb2.HealthCheckResponse.NOT_SERVING
    assert now_ready.status == health_pb2.HealthCheckResponse.SERVING
    channel.close()
    server.stop(None)


def test_handlers_accepts_a_custom_health_check_too():
    ready = {"value": False}
    handlers = GrpcGateway({"payments": _bus("payments")}).handlers(
        health_check=lambda: ready["value"]
    )
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
    server.add_generic_rpc_handlers(handlers)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    stub: Any = health_pb2_grpc.HealthStub(channel)

    assert (
        stub.Check(health_pb2.HealthCheckRequest(service="")).status
        == health_pb2.HealthCheckResponse.NOT_SERVING
    )
    channel.close()
    server.stop(None)


# --- graceful shutdown on SIGTERM/SIGINT ------------------------------------


def test_run_hooks_sigterm_and_sigint_by_default(monkeypatch):
    hooked: list[int] = []
    original = signal.signal
    monkeypatch.setattr(
        signal, "signal", lambda sig, handler: (hooked.append(sig), original(sig, handler))[1]
    )

    class ImmediateTermination:
        def wait_for_termination(self):
            return None

    gateway = GrpcGateway({"payments": _bus("payments")})
    fake_server = ImmediateTermination()
    monkeypatch.setattr(gateway, "server", lambda **_kwargs: fake_server)
    monkeypatch.setattr(fake_server, "add_insecure_port", lambda _addr: 0, raising=False)
    monkeypatch.setattr(fake_server, "start", lambda: None, raising=False)

    gateway.run("127.0.0.1:0")

    assert set(hooked) == set(SHUTDOWN_SIGNALS)


def test_handle_signals_false_skips_the_hook(monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(signal, "signal", lambda sig, handler: calls.append(sig))

    class ImmediateTermination:
        def wait_for_termination(self):
            return None

        def add_insecure_port(self, _addr):
            return 0

        def start(self):
            return None

    gateway = GrpcGateway({"payments": _bus("payments")})
    monkeypatch.setattr(gateway, "server", lambda **_kwargs: ImmediateTermination())

    gateway.run("127.0.0.1:0", handle_signals=False)

    assert calls == []


def test_sigterm_drains_an_in_flight_call_before_the_server_stops():
    """The real thing, not a mock: a slow Feature is mid-`execute` when SIGTERM
    fires; the call still completes and `server.stop` only runs after.
    """
    started = futures.Future()
    stopped_with: list[float | None] = []

    class SlowFeature(Feature):
        def execute(self, dto: Ask) -> Response:
            started.set_result(True)
            time.sleep(0.4)
            return Response(total=dto.n)

    bus = UseFramework("slow", log_after_execution=False)
    bus.feature(Ask)(SlowFeature)
    bus.build_root_bus()

    gateway = GrpcGateway({"slow": bus})
    server = gateway.server(reflection=False)
    real_stop = server.stop
    server.stop = lambda grace: (stopped_with.append(grace), real_stop(grace))[1]
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()

    def send_sigterm_once_in_flight():
        started.result(timeout=2)
        import os

        os.kill(os.getpid(), signal.SIGTERM)

    original_handler = signal.getsignal(signal.SIGTERM)
    from sincpro_framework.entrypoints.grpc.entrypoint import _hook_graceful_shutdown

    _hook_graceful_shutdown(server, "slow", grace=2.0)
    trigger = futures.ThreadPoolExecutor(max_workers=1)
    trigger.submit(send_sigterm_once_in_flight)

    with GrpcClient(f"127.0.0.1:{port}") as client:
        result = client.call("/slow.Features/Ask", {"n": 7})

    assert result == {"total": 7.0}
    assert stopped_with == [2.0]
    trigger.shutdown(wait=True)
    signal.signal(signal.SIGTERM, original_handler)
    server.stop(None)


# --- entrypoint_rpc: /healthz served by default ------------------------------


def test_rpc_app_serves_healthz_by_default():
    from starlette.testclient import TestClient

    from sincpro_framework.entrypoints.rpc import RpcGateway

    bus = _bus("pay")
    client = TestClient(RpcGateway({"pay": bus}).app())

    reply = client.get("/healthz")

    assert reply.status_code == 200
    assert reply.json() == {"status": "ok"}


def test_rpc_health_path_none_drops_the_route():
    from starlette.testclient import TestClient

    from sincpro_framework.entrypoints.rpc import RpcGateway

    client = TestClient(RpcGateway({"pay": _bus("pay")}).app(health_path=None))

    assert client.get("/healthz").status_code == 404


def test_rpc_is_healthy_reflects_every_registered_bus():
    from sincpro_framework.entrypoints.rpc import RpcGateway

    gateway = RpcGateway({"pay": _bus("pay"), "qr": _bus("qr")})

    assert gateway.is_healthy() is True
