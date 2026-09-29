"""The gRPC surface is declared, and named the way the gRPC ecosystem expects (PRD_15 §3.2).

The incidents: every Feature of a bus became a public gRPC method by being registered — nothing
had to be said to publish it — and the public name carried the layer
(`/billing.Features/CommandIssueInvoice`), so promoting a Feature to an ApplicationService renamed
a method every client had generated a stub for. The names are now AIP's: package `billing.v1`,
service `BillingService`, method `IssueInvoice`; the surface is only what is declared unless the
composition asks for the catalog.
"""

from collections.abc import Iterator
from typing import Any

import grpc as grpcio
import pytest
from google.protobuf import descriptor_pb2
from grpc_reflection.v1alpha import reflection_pb2  # pyright: ignore[reportMissingImports]

from sincpro_framework import ApplicationService, DataTransferObject, Feature, UseFramework
from sincpro_framework.entrypoints.exposure import (
    Exposure,
    ExposureRefused,
    GrpcBinding,
    grpc,
    internal,
)
from sincpro_framework.entrypoints.grpc import GrpcGateway
from sincpro_framework.entrypoints.grpc.client import GrpcClient


class CommandIssueInvoice(DataTransferObject):
    total: int


class QueryInvoice(DataTransferObject):
    number: str


class CommandCancelInvoice(DataTransferObject):
    number: str


class CommandReconcile(DataTransferObject):
    pass


class Issued(DataTransferObject):
    number: str


def _billing(name: str = "billing") -> UseFramework:
    billing = UseFramework(name, log_after_execution=False)

    @billing.feature(CommandIssueInvoice)
    @grpc()
    class IssueInvoice(Feature):
        def execute(self, dto: CommandIssueInvoice) -> Issued:
            return Issued(number=f"F-{dto.total}")

    @billing.feature(QueryInvoice)
    @grpc(service="InvoiceReadService")
    class GetInvoice(Feature):
        def execute(self, dto: QueryInvoice) -> Issued:
            return Issued(number=dto.number)

    @billing.app_service(CommandCancelInvoice)
    class CancelInvoice(ApplicationService):
        def execute(self, dto: CommandCancelInvoice) -> Issued:
            return Issued(number=dto.number)

    @billing.feature(CommandReconcile)
    @internal
    class Reconcile(Feature):
        def execute(self, dto: CommandReconcile) -> None:
            return None

    return billing


def test_only_what_is_declared_is_served_by_default() -> None:
    gateway = GrpcGateway({"billing": _billing()}, unguarded=True)

    assert set(gateway.methods()) == {
        "/billing.v1.BillingService/IssueInvoice",
        "/billing.v1.InvoiceReadService/Invoice",
    }


def test_the_catalog_mode_publishes_every_use_case_with_the_same_names() -> None:
    """The layer never reaches a public name: an ApplicationService sits in the same service
    as a Feature, and `@internal` stays off the wire."""
    gateway = GrpcGateway({"billing": _billing()}, exposure=Exposure.CATALOG, unguarded=True)

    assert set(gateway.methods()) == {
        "/billing.v1.BillingService/IssueInvoice",
        "/billing.v1.InvoiceReadService/Invoice",
        "/billing.v1.BillingService/CancelInvoice",
    }


def test_bind_publishes_a_use_case_without_a_decorator() -> None:
    gateway = GrpcGateway({"billing": _billing()}, unguarded=True).bind(
        CommandCancelInvoice, GrpcBinding(method="Void")
    )

    assert "/billing.v1.BillingService/Void" in gateway.methods()


def test_the_group_sets_the_package_and_its_major_version() -> None:
    versioned = GrpcGateway({"billing": _billing()}, unguarded=True).group(
        "billing", version="v2"
    )
    packaged = GrpcGateway({"billing": _billing()}, unguarded=True).group(
        "billing", package="acme.billing"
    )

    assert "/billing.v2.BillingService/IssueInvoice" in versioned.methods()
    assert "/acme.billing.v1.BillingService/IssueInvoice" in packaged.methods()


def test_the_service_is_the_pascal_case_of_the_alias() -> None:
    gateway = GrpcGateway({"sales_orders": _billing("orders")}, unguarded=True)

    assert "/sales_orders.v1.SalesOrdersService/IssueInvoice" in gateway.methods()


def test_two_methods_answering_one_name_after_stripping_fail_the_build() -> None:
    """`CommandInvoice` and `QueryInvoice` both become `Invoice` in one service."""

    class CommandInvoice(DataTransferObject):
        number: str

    bus = UseFramework("clashing", log_after_execution=False)

    @bus.feature(CommandInvoice)
    @grpc(service="InvoiceReadService")
    class WriteInvoice(Feature):
        def execute(self, dto: CommandInvoice) -> None:
            return None

    @bus.feature(QueryInvoice)
    @grpc(service="InvoiceReadService")
    class ReadInvoice(Feature):
        def execute(self, dto: QueryInvoice) -> None:
            return None

    with pytest.raises(ExposureRefused) as refused:
        GrpcGateway({"clashing": bus}, unguarded=True).methods()

    said = str(refused.value)
    assert "/clashing.v1.InvoiceReadService/Invoice" in said
    assert "CommandInvoice" in said and "QueryInvoice" in said


def test_a_name_that_is_no_proto_identifier_fails_the_build() -> None:
    gateway = GrpcGateway({"billing": _billing()}, unguarded=True).override(
        CommandIssueInvoice, method="issue-invoice"
    )

    with pytest.raises(ExposureRefused, match="issue-invoice"):
        gateway.methods()


def test_a_bus_nobody_guards_is_refused_unless_said() -> None:
    with pytest.raises(ExposureRefused, match="unguarded=True"):
        GrpcGateway({"billing": _billing()}).methods()


def test_the_manifest_names_each_method_by_its_path() -> None:
    manifest = GrpcGateway({"billing": _billing()}, unguarded=True).manifest()

    assert [(one.wire, one.name, one.method) for one in manifest] == [
        ("grpc", "/billing.v1.BillingService/IssueInvoice", "IssueInvoice"),
        ("grpc", "/billing.v1.InvoiceReadService/Invoice", "Invoice"),
    ]


def test_describe_and_the_proto_carry_the_new_names() -> None:
    gateway = GrpcGateway({"billing": _billing()}, unguarded=True)
    services = {one["name"]: one for one in gateway.describe()["services"]}
    files = gateway.proto_files()
    source = files["billing/v1/billing.proto"]

    assert set(services) == {"billing.v1.BillingService", "billing.v1.InvoiceReadService"}
    assert services["billing.v1.BillingService"]["methods"][0]["path"] == (
        "/billing.v1.BillingService/IssueInvoice"
    )
    assert set(files) == {"billing/v1/billing.proto", "sincpro.proto"}
    assert "package billing.v1;" in source
    assert "service BillingService {" in source and "service InvoiceReadService {" in source
    assert "  rpc IssueInvoice(google.protobuf.Struct) returns (google.protobuf.Struct);" in (
        source
    )


def test_write_proto_files_lays_out_the_package_directories(tmp_path: Any) -> None:
    written = GrpcGateway({"billing": _billing()}, unguarded=True).write_proto_files(tmp_path)

    assert (tmp_path / "billing" / "v1" / "billing.proto") in written
    assert (
        (tmp_path / "billing" / "v1" / "billing.proto")
        .read_text()
        .startswith("// Generated by")
    )


@pytest.fixture
def served() -> Iterator[GrpcClient]:
    server = GrpcGateway({"billing": _billing()}, unguarded=True).server(max_workers=2)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    client = GrpcClient(f"127.0.0.1:{port}")
    try:
        yield client
    finally:
        client.close()
        server.stop(None)


def test_the_new_path_answers_and_the_layer_path_is_gone(served: GrpcClient) -> None:
    assert served.call("/billing.v1.BillingService/IssueInvoice", {"total": 3}) == {
        "number": "F-3"
    }
    with pytest.raises(grpcio.RpcError) as gone:
        served.call("/billing.Features/CommandIssueInvoice", {"total": 3})
    assert gone.value.code() is grpcio.StatusCode.UNIMPLEMENTED  # pyright: ignore


def test_reflection_describes_the_package_its_services_and_methods(
    served: GrpcClient,
) -> None:
    info = served.channel.stream_stream(
        "/grpc.reflection.v1.ServerReflection/ServerReflectionInfo",
        request_serializer=reflection_pb2.ServerReflectionRequest.SerializeToString,
        response_deserializer=reflection_pb2.ServerReflectionResponse.FromString,
    )

    def ask(**request: Any) -> Any:
        return next(iter(info(iter([reflection_pb2.ServerReflectionRequest(**request)]))))

    listed = {one.name for one in ask(list_services="").list_services_response.service}
    described = ask(file_containing_symbol="billing.v1.BillingService")
    files = [
        descriptor_pb2.FileDescriptorProto.FromString(one)
        for one in described.file_descriptor_response.file_descriptor_proto
    ]
    methods = {
        (service.name, method.name)
        for file in files
        if file.package == "billing.v1"
        for service in file.service
        for method in service.method
    }

    assert {"billing.v1.BillingService", "billing.v1.InvoiceReadService"} <= listed
    assert methods == {("BillingService", "IssueInvoice"), ("InvoiceReadService", "Invoice")}
