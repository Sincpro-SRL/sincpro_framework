"""What a gRPC server owes its callers beyond the answer (PRD_15 §3.3, known issues 3, 4, 5a).

The incidents: a call whose caller had already given up still ran — and charged — because the
deadline was never looked at; a burst of calls queued silently behind the worker pool until the
callers' own deadlines fired, instead of being told to back off; `grpcurl` and every client on
the v1 reflection service found nothing, because only v1alpha was served.
"""

import threading
import time
from collections.abc import Iterator
from typing import Any

import grpc
import pytest
from grpc_reflection.v1alpha import reflection_pb2  # pyright: ignore[reportMissingImports]

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.entrypoints.exposure import Exposure
from sincpro_framework.entrypoints.grpc import GrpcGateway
from sincpro_framework.entrypoints.grpc.client import GrpcClient
from sincpro_framework.transport import grpc as transport

ASK = "/billing.v1.BillingService/Charge"
HOLD = "/billing.v1.BillingService/Hold"


class CommandCharge(DataTransferObject):
    amount: int


class ResponseCharge(DataTransferObject):
    charged: int
    deadline: float | None = None


class CommandHold(DataTransferObject):
    label: str


charges: list[int] = []
entered = threading.Semaphore(0)
release = threading.Event()


def _billing() -> UseFramework:
    billing = UseFramework("behaving-billing", log_after_execution=False)

    @billing.feature(CommandCharge)
    class Charge(Feature):
        def execute(self, dto: CommandCharge) -> ResponseCharge:
            charges.append(dto.amount)
            return ResponseCharge(charged=dto.amount, deadline=self.context.get("deadline"))

    @billing.feature(CommandHold)
    class Hold(Feature):
        def execute(self, dto: CommandHold) -> ResponseCharge:
            entered.release()
            release.wait(5)
            return ResponseCharge(charged=0)

    billing.build_root_bus()
    return billing


def _gateway() -> GrpcGateway:
    return GrpcGateway({"billing": _billing()}, exposure=Exposure.CATALOG, unguarded=True)


class _SlowBeforeTheHandler(grpc.ServerInterceptor):
    """Middleware that takes longer than the caller was willing to wait — a slow token
    introspection, a cold connection — and then hands the call on."""

    def intercept_service(self, continuation: Any, handler_call_details: Any) -> Any:
        inner = continuation(handler_call_details)

        def slow(request: Any, context: Any) -> Any:
            time.sleep(0.4)
            return inner.unary_unary(request, context)

        return grpc.unary_unary_rpc_method_handler(
            slow,
            request_deserializer=inner.request_deserializer,
            response_serializer=inner.response_serializer,
        )


@pytest.fixture(autouse=True)
def _fresh() -> Iterator[None]:
    global entered
    charges.clear()
    release.clear()
    entered = threading.Semaphore(0)
    yield
    release.set()


def _serve(server: Any) -> tuple[Any, GrpcClient]:
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    return server, GrpcClient(f"127.0.0.1:{port}")


def test_a_call_past_its_deadline_never_runs_the_use_case() -> None:
    server, client = _serve(
        _gateway().server(interceptors=[_SlowBeforeTheHandler()], reflection=False)
    )
    with client, pytest.raises(grpc.RpcError) as gave_up:
        client.call(ASK, {"amount": 10}, timeout=0.2)
    time.sleep(0.5)
    server.stop(None)

    assert gave_up.value.code() is grpc.StatusCode.DEADLINE_EXCEEDED  # pyright: ignore
    assert charges == []


def test_the_deadline_reaches_the_use_case_as_an_absolute_time() -> None:
    server, client = _serve(_gateway().server(reflection=False))
    before = time.time()
    with client:
        with_deadline = client.call(ASK, {"amount": 1}, timeout=30)
        without = client.call(ASK, {"amount": 2})
    server.stop(None)

    assert before + 25 < with_deadline["deadline"] <= before + 30.5
    assert without.get("deadline") is None


def _flood(client: GrpcClient, holders: int) -> list[threading.Thread]:
    started = [
        threading.Thread(target=client.call, args=(HOLD, {"label": str(one)}), daemon=True)
        for one in range(holders)
    ]
    for one in started:
        one.start()
    return started


def _drain(holding: list[threading.Thread]) -> None:
    release.set()
    for one in holding:
        one.join(timeout=5)


def test_past_the_concurrency_limit_the_server_answers_resource_exhausted() -> None:
    server, client = _serve(
        _gateway().server(max_workers=1, maximum_concurrent_rpcs=1, reflection=False)
    )
    with client:
        holding = _flood(client, 1)
        assert entered.acquire(timeout=5)
        with pytest.raises(grpc.RpcError) as refused:
            client.call(ASK, {"amount": 1}, timeout=5)
        _drain(holding)
    server.stop(None)

    assert refused.value.code() is grpc.StatusCode.RESOURCE_EXHAUSTED  # pyright: ignore
    assert charges == []


def test_the_limit_defaults_to_twice_the_workers() -> None:
    """One worker, two admitted: one running, one queued behind it — the third is refused."""
    server, client = _serve(_gateway().server(max_workers=1, reflection=False))
    with client:
        holding = _flood(client, 2)
        assert entered.acquire(timeout=5)
        time.sleep(0.2)
        with pytest.raises(grpc.RpcError) as refused:
            client.call(ASK, {"amount": 1}, timeout=5)
        _drain(holding)
    server.stop(None)

    assert refused.value.code() is grpc.StatusCode.RESOURCE_EXHAUSTED  # pyright: ignore


def test_the_transport_server_carries_the_same_limit() -> None:
    """Remote execution's host stands on `transport.grpc.server` too: the default is there."""
    server = transport.server(max_workers=1)
    handler = grpc.method_handlers_generic_handler(
        "probe.Hold", {"Wait": grpc.unary_unary_rpc_method_handler(_hold_raw)}
    )
    server.add_generic_rpc_handlers((handler,))
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    wait = channel.unary_unary("/probe.Hold/Wait")
    pending = [wait.future(b"", timeout=5) for _ in range(2)]
    assert entered.acquire(timeout=5)
    time.sleep(0.2)
    with pytest.raises(grpc.RpcError) as refused:
        wait(b"", timeout=5)
    release.set()
    [one.result() for one in pending]
    channel.close()
    server.stop(None)

    assert refused.value.code() is grpc.StatusCode.RESOURCE_EXHAUSTED  # pyright: ignore


def _hold_raw(_request: bytes, _context: Any) -> bytes:
    entered.release()
    release.wait(5)
    return b""


@pytest.mark.parametrize("version", ["v1", "v1alpha"])
def test_reflection_lists_and_describes_every_method_on_both_versions(version: str) -> None:
    """v1 and v1alpha share their messages field for field — one client speaks either."""
    server, client = _serve(_gateway().server())
    info = client.channel.stream_stream(
        f"/grpc.reflection.{version}.ServerReflection/ServerReflectionInfo",
        request_serializer=reflection_pb2.ServerReflectionRequest.SerializeToString,
        response_deserializer=reflection_pb2.ServerReflectionResponse.FromString,
    )

    def ask(**request: Any) -> Any:
        return next(iter(info(iter([reflection_pb2.ServerReflectionRequest(**request)]))))

    with client:
        listed = {one.name for one in ask(list_services="").list_services_response.service}
        described = ask(
            file_containing_symbol="billing.v1.BillingService"
        ).file_descriptor_response
        itself = ask(file_containing_symbol=f"grpc.reflection.{version}.ServerReflection")
    server.stop(None)

    from google.protobuf import descriptor_pb2

    files = [
        descriptor_pb2.FileDescriptorProto.FromString(one)
        for one in described.file_descriptor_proto
    ]
    methods = {
        method.name
        for file in files
        for service in file.service
        if file.package == "billing.v1" and service.name == "BillingService"
        for method in service.method
    }
    assert {
        "billing.v1.BillingService",
        "sincpro.Introspection",
        "grpc.reflection.v1.ServerReflection",
        "grpc.reflection.v1alpha.ServerReflection",
    } <= listed
    assert methods == {"Charge", "Hold"}
    assert itself.file_descriptor_response.file_descriptor_proto


def test_a_message_over_the_limit_is_refused_before_the_use_case_runs() -> None:
    server, client = _serve(_gateway().server(max_message_bytes=1024, reflection=False))
    with client, pytest.raises(grpc.RpcError) as refused:
        client.call(HOLD, {"label": "x" * 4096}, timeout=5)
    server.stop(None)

    assert refused.value.code() is grpc.StatusCode.RESOURCE_EXHAUSTED  # pyright: ignore


def test_the_server_options_default_to_what_a_load_balancer_needs() -> None:
    """4 MiB both ways, connections recycled every five minutes, keepalive on — each one
    replaced by the caller's option of the same key, never duplicated."""
    from sincpro_framework.entrypoints.grpc.wire import server_options

    defaults = dict(server_options())
    chosen = server_options([("grpc.max_connection_age_ms", 60_000)], max_message_bytes=8)
    kept = dict(server_options(max_connection_age=None))

    assert defaults["grpc.max_receive_message_length"] == 4 * 1024 * 1024
    assert defaults["grpc.max_send_message_length"] == 4 * 1024 * 1024
    assert defaults["grpc.max_connection_age_ms"] == 300_000
    assert defaults["grpc.keepalive_time_ms"] > 0
    assert [value for key, value in chosen if key == "grpc.max_connection_age_ms"] == [60_000]
    assert dict(chosen)["grpc.max_receive_message_length"] == 8
    assert "grpc.max_connection_age_ms" not in kept
