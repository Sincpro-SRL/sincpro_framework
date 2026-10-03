"""A hand-written gRPC servicer runs the path the generated one runs (PRD_15 §1.2, §3.4).

The incident this prevents: a project that wrote one servicer of its own re-implemented auth and
errors in it — or skipped them. `bus_call` is the one path both take: metadata into the context,
the caller's credentials, the deadline pre-check, the bus (access guard, idempotency, the
Feature's span), and a `google.rpc.Status` on failure. A method path the gateway already serves
cannot be written by hand as well: which one answered would depend on mounting order.
"""

import time
from collections.abc import Iterator
from concurrent import futures
from typing import Any

import grpc
import pytest
from google.protobuf import descriptor_pb2, descriptor_pool
from google.protobuf.struct_pb2 import Struct
from grpc_reflection.v1alpha import reflection_pb2  # pyright: ignore[reportMissingImports]
from grpc_status import rpc_status  # pyright: ignore[reportMissingImports]

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.auth import (
    AccessControl,
    Identity,
    Permission,
    StaticProvider,
    current_identity,
)
from sincpro_framework.context import CHAIN_KEYS, EXECUTION_ID
from sincpro_framework.ddd.exceptions import ContractViolation
from sincpro_framework.entrypoints.exposure import ExposureRefused
from sincpro_framework.entrypoints.exposure import grpc as expose
from sincpro_framework.entrypoints.grpc import GrpcGateway, bus_call, framework_interceptors
from sincpro_framework.entrypoints.grpc.wire import scalar_to_struct, struct_to_scalar

GENERATED = "/parity.v1.ParityService/IssueInvoice"
HAND_WRITTEN = "/parity.v1.HandService/IssueInvoice"


class Perm(Permission):
    ISSUE = "parity.invoice.issue"


class CommandIssueInvoice(DataTransferObject):
    total: int


class Issued(DataTransferObject):
    number: str
    by: str
    tenant: str | None = None


ran: list[int] = []


def _parity() -> UseFramework:
    issuer = Identity.user("user:1", permissions={Perm.ISSUE})
    auth = AccessControl[Perm](providers=[StaticProvider({"t-issuer": issuer})])
    bus = UseFramework("parity", log_after_execution=False)

    @bus.feature(CommandIssueInvoice)
    @auth.requires(Perm.ISSUE)
    @expose()
    class IssueInvoice(Feature):
        def execute(self, dto: CommandIssueInvoice) -> Issued:
            ran.append(dto.total)
            if dto.total < 0:
                raise ContractViolation("an invoice has to balance")
            return Issued(
                number=f"F-{dto.total}",
                by=current_identity().subject,
                tenant=self.context.get("tenant"),
            )

    auth.on(bus)
    return bus


def _hand_written(bus: UseFramework) -> Any:
    """What a project writes: its own method, its own DTO construction, the bus through
    `bus_call`."""

    def issue(request: Struct, context: grpc.ServicerContext) -> Struct:
        fields = struct_to_scalar(request)
        return bus_call(context, bus, lambda: CommandIssueInvoice(total=fields["total"]))

    return grpc.method_handlers_generic_handler(
        "parity.v1.HandService",
        {
            "IssueInvoice": grpc.unary_unary_rpc_method_handler(
                issue,
                request_deserializer=Struct.FromString,
                response_serializer=Struct.SerializeToString,
            )
        },
    )


@pytest.fixture
def channel() -> Iterator[grpc.Channel]:
    bus = _parity()
    server = GrpcGateway({"parity": bus}).server(max_workers=4, reflection=False)
    server.add_generic_rpc_handlers((_hand_written(bus),))
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    opened = grpc.insecure_channel(f"127.0.0.1:{port}")
    ran.clear()
    yield opened
    opened.close()
    server.stop(None)


def _answer(channel: grpc.Channel, path: str, payload: dict[str, Any], **call: Any) -> Any:
    """What a caller observes: the answer, or the code, message and rich status."""
    method = channel.unary_unary(
        path,
        request_serializer=Struct.SerializeToString,
        response_deserializer=Struct.FromString,
    )
    try:
        return ("ok", struct_to_scalar(method(scalar_to_struct(payload), **call)))
    except grpc.RpcError as failed:
        error: Any = failed
        status = rpc_status.from_call(error)
        trailing = {
            key: value for key, value in error.trailing_metadata() if not key.endswith("-bin")
        }
        return (error.code(), error.details(), status, trailing)


ISSUER = (("authorization", "Bearer t-issuer"), ("sp-ctx-tenant", "acme"))


@pytest.mark.parametrize(
    ("payload", "metadata"),
    [
        ({"total": 5}, ISSUER),
        ({"total": 5}, ()),
        ({"total": -1}, ISSUER),
        ({"total": "lots"}, ISSUER),
    ],
    ids=["answered", "unauthenticated", "domain", "invalid"],
)
def test_the_helper_answers_exactly_what_the_generated_method_answers(
    channel: grpc.Channel, payload: dict[str, Any], metadata: tuple[Any, ...]
) -> None:
    generated = _answer(channel, GENERATED, payload, metadata=metadata)
    hand_written = _answer(channel, HAND_WRITTEN, payload, metadata=metadata)

    assert generated == hand_written
    assert generated[0] != "ok" or generated[1] == {
        "number": "F-5",
        "by": "user:1",
        "tenant": "acme",
    }


def test_the_helper_runs_under_the_same_feature_span(
    channel: grpc.Channel, otel_setup
) -> None:
    exporter = otel_setup

    def spans_of(path: str) -> list[tuple[str, Any]]:
        """Each span's attributes but its execution's own identity — two calls are two executions."""
        exporter.clear()
        _answer(channel, path, {"total": 5}, metadata=ISSUER)
        spans = [
            dict(span.attributes or {}) | {"name": span.name}
            for span in exporter.get_finished_spans()
            if (span.attributes or {}).get("sincpro.use_case") == "CommandIssueInvoice"
        ]
        assert all(EXECUTION_ID in attributes for attributes in spans)
        return [
            (
                attributes.pop("name"),
                {k: v for k, v in attributes.items() if k not in CHAIN_KEYS},
            )
            for attributes in spans
        ]

    generated = spans_of(GENERATED)

    assert generated and generated == spans_of(HAND_WRITTEN)


def test_the_helper_never_runs_a_call_past_its_deadline() -> None:
    bus = _parity()

    class Late:
        def time_remaining(self) -> float:
            return -1.0

        def invocation_metadata(self) -> tuple[Any, ...]:
            return ISSUER

        def auth_context(self) -> dict[str, Any]:
            return {}

        def abort_with_status(self, status: Any) -> None:
            raise RuntimeError(status.code)

    ran.clear()
    with pytest.raises(RuntimeError) as aborted:
        bus_call(Late(), bus, CommandIssueInvoice(total=1))

    assert aborted.value.args[0] is grpc.StatusCode.DEADLINE_EXCEEDED
    assert ran == []


def test_to_response_shapes_what_the_servicer_returns() -> None:
    bus = _parity()

    class OnTime:
        def time_remaining(self) -> None:
            return None

        def invocation_metadata(self) -> tuple[Any, ...]:
            return ISSUER

        def auth_context(self) -> dict[str, Any]:
            return {}

    number = bus_call(
        OnTime(), bus, CommandIssueInvoice(total=9), to_response=lambda one: one.number
    )

    assert number == "F-9"


class _Slow(grpc.ServerInterceptor):
    """Middleware slower than the caller was willing to wait, once the call was dispatched."""

    def intercept_service(self, continuation: Any, handler_call_details: Any) -> Any:
        inner = continuation(handler_call_details)

        def slow(request: Any, context: Any) -> Any:
            time.sleep(0.4)
            return inner.unary_unary(request, context)

        return grpc.unary_unary_rpc_method_handler(slow)


def test_the_framework_interceptors_refuse_a_call_whose_deadline_passed() -> None:
    """A hand-written servicer that never calls the bus still gets the deadline pre-check."""
    touched: list[bool] = []

    def plain(_request: bytes, _context: Any) -> bytes:
        touched.append(True)
        return b""

    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=2),
        interceptors=[_Slow(), *framework_interceptors()],
    )
    server.add_generic_rpc_handlers(
        (
            grpc.method_handlers_generic_handler(
                "plain.v1.PlainService", {"Ping": grpc.unary_unary_rpc_method_handler(plain)}
            ),
        )
    )
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    with grpc.insecure_channel(f"127.0.0.1:{port}") as opened:
        with pytest.raises(grpc.RpcError) as gave_up:
            opened.unary_unary("/plain.v1.PlainService/Ping")(b"", timeout=0.2)
        time.sleep(0.4)
        answered = opened.unary_unary("/plain.v1.PlainService/Ping")(b"", timeout=5)
    server.stop(None)

    assert gave_up.value.code() is grpc.StatusCode.DEADLINE_EXCEEDED  # pyright: ignore
    assert answered == b"" and touched == [True]


def _hand_written_file(service: str) -> Any:
    """A compiled `*_pb2.DESCRIPTOR` without protoc: the file a hand-written servicer ships."""
    file = descriptor_pb2.FileDescriptorProto(
        name=f"hand/{service}.proto",
        package="parity.v1",
        syntax="proto3",
        dependency=["google/protobuf/struct.proto"],
    )
    method = file.service.add(name=service).method.add(name="IssueInvoice")
    method.input_type = method.output_type = ".google.protobuf.Struct"
    pool = descriptor_pool.DescriptorPool()
    pool.Add(
        descriptor_pb2.FileDescriptorProto.FromString(Struct.DESCRIPTOR.file.serialized_pb)
    )
    pool.Add(file)
    return pool.FindFileByName(file.name)


def test_mount_merges_the_hand_written_services_into_reflection() -> None:
    bus = _parity()
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=2),
        interceptors=framework_interceptors(),
    )
    server.add_generic_rpc_handlers((_hand_written(bus),))
    GrpcGateway({"parity": bus}).mount(
        server, reflection_extra=[_hand_written_file("HandService")]
    )
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    with grpc.insecure_channel(f"127.0.0.1:{port}") as opened:
        info = opened.stream_stream(
            "/grpc.reflection.v1.ServerReflection/ServerReflectionInfo",
            request_serializer=reflection_pb2.ServerReflectionRequest.SerializeToString,
            response_deserializer=reflection_pb2.ServerReflectionResponse.FromString,
        )
        request = reflection_pb2.ServerReflectionRequest(list_services="")
        listed = next(iter(info(iter([request])))).list_services_response.service
        hand = _answer(opened, HAND_WRITTEN, {"total": 2}, metadata=ISSUER)
        generated = _answer(opened, GENERATED, {"total": 2}, metadata=ISSUER)
    server.stop(None)

    assert {"parity.v1.ParityService", "parity.v1.HandService"} <= {
        one.name for one in listed
    }
    assert hand == generated == ("ok", {"number": "F-2", "by": "user:1", "tenant": "acme"})


def test_a_hand_written_path_the_gateway_also_serves_fails_the_build() -> None:
    bus = _parity()
    server = grpc.server(futures.ThreadPoolExecutor(1))

    with pytest.raises(ExposureRefused, match="/parity.v1.ParityService/IssueInvoice"):
        GrpcGateway({"parity": bus}).mount(
            server, reflection_extra=[_hand_written_file("ParityService")]
        )
