"""Remote execution and the entrypoints are two doors: one keeps a codebase whole across machines,
the other publishes a contract to others. Neither opens the other.

Before, they were one server both ways: hosting a context for the project's own services
(`bus.serve`) also published its whole catalog as public gRPC methods, and every public
`GrpcGateway` also answered `/sincpro.Contexts/Execute` — the door that runs a whole context.
"""

from collections.abc import Iterator

import grpc
import pytest

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.entrypoints.adapters.grpc import GrpcGateway
from sincpro_framework.entrypoints.domain.surface import Exposure
from sincpro_framework.remote_execution import Attach
from sincpro_framework.remote_execution.adapters.grpc import PATH


class CommandPing(DataTransferObject):
    pass


class ResponsePing(DataTransferObject):
    where: str


def _billing(name: str, where: str) -> UseFramework:
    bus = UseFramework(name, log_after_execution=False)

    @bus.feature(CommandPing)
    class Ping(Feature):
        def execute(self, dto: CommandPing) -> ResponsePing:
            return ResponsePing(where=where)

    return bus


def _code_of(address: str, method: str, streaming: bool = False) -> grpc.StatusCode:
    """What a raw call to `method` on `address` answers — OK or the refusal's code."""
    with grpc.insecure_channel(address) as channel:
        try:
            if streaming:
                call = channel.stream_stream(method)
                list(call(iter([b""]), timeout=5))
            else:
                channel.unary_unary(method)(b"", timeout=5)
        except grpc.RpcError as error:
            return error.code()  # type: ignore[attr-defined]
    return grpc.StatusCode.OK


@pytest.fixture
def internal_host() -> Iterator[str]:
    host = _billing("doors-billing", "internal").serve("127.0.0.1:0", Attach.THREAD)
    yield host.address
    host.stop(0)


def test_hosting_a_context_for_the_projects_services_publishes_none_of_its_use_cases(
    internal_host,
):
    """The internal door answers remote execution only: the catalog's public methods, the
    introspection document and reflection are not there to be discovered."""
    assert _code_of(internal_host, "/doors_billing.v1.DoorsBillingService/Ping") == (
        grpc.StatusCode.UNIMPLEMENTED
    )
    assert _code_of(internal_host, "/sincpro.Introspection/Describe") == (
        grpc.StatusCode.UNIMPLEMENTED
    )
    assert _code_of(
        internal_host,
        "/grpc.reflection.v1alpha.ServerReflection/ServerReflectionInfo",
        streaming=True,
    ) == (grpc.StatusCode.UNIMPLEMENTED)


def test_the_internal_host_still_answers_the_projects_own_calls(internal_host):
    caller = _billing("doors-billing", "caller")
    caller.hosted_by(f"grpc://{internal_host}")

    assert caller(CommandPing(), ResponsePing) == ResponsePing(where="internal")


def test_the_internal_host_is_probed_by_the_standard_health_check(internal_host):
    from grpc_health.v1 import health_pb2

    with grpc.insecure_channel(internal_host) as channel:
        check = channel.unary_unary(
            "/grpc.health.v1.Health/Check",
            request_serializer=health_pb2.HealthCheckRequest.SerializeToString,
            response_deserializer=health_pb2.HealthCheckResponse.FromString,
        )
        for service in ("", "sincpro.Contexts"):
            answer = check(health_pb2.HealthCheckRequest(service=service), timeout=5)
            assert answer.status == health_pb2.HealthCheckResponse.SERVING


def test_a_public_gateway_does_not_open_the_internal_door():
    public = _billing("doors-public", "public")
    server = GrpcGateway({"doors_public": public}, exposure=Exposure.CATALOG, unguarded=True)
    server = server.server()
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    try:
        assert _code_of(f"127.0.0.1:{port}", PATH, streaming=True) == (
            grpc.StatusCode.UNIMPLEMENTED
        )
    finally:
        server.stop(0)


def test_both_doors_share_one_port_only_when_asked_to():
    """A deployment that wants one port mounts the internal door on the public server, on
    purpose — and then both answer."""
    from sincpro_framework.remote_execution import open_host

    hosted = _billing("doors-shared", "shared")
    gateway = GrpcGateway({"doors_shared": hosted}, exposure=Exposure.CATALOG, unguarded=True)
    server = gateway.server()
    open_host([hosted]).mount(server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    try:
        caller = _billing("doors-shared", "caller")
        caller.hosted_by(f"grpc://127.0.0.1:{port}")

        assert caller(CommandPing(), ResponsePing) == ResponsePing(where="shared")
        assert _code_of(f"127.0.0.1:{port}", "/doors_shared.v1.DoorsSharedService/Ping") != (
            grpc.StatusCode.UNIMPLEMENTED
        )
    finally:
        server.stop(0)
