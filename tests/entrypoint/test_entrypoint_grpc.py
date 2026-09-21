"""entrypoint_grpc: bus catalog as gRPC services over google.protobuf.Struct."""

import json
from collections.abc import Iterator
from typing import Any

import grpc
import pytest

from sincpro_framework import ApplicationService, DataTransferObject, Feature, UseFramework
from sincpro_framework.ddd import ValueObject
from sincpro_framework.ddd.exceptions import ContractViolation
from sincpro_framework.entrypoints.grpc import GrpcGateway
from sincpro_framework.entrypoints.grpc.client import GrpcClient
from sincpro_framework.entrypoints.grpc.proto import DESCRIBE_PATH
from sincpro_framework.entrypoints.grpc.wire import context_from_metadata


class ValidateCard(DataTransferObject):
    card_number: str
    cvv: str


class ValidateCardResponse(DataTransferObject):
    valid: bool
    card_number: str


class ChargePayment(DataTransferObject):
    amount: float


class ChargePaymentResponse(DataTransferObject):
    charged: bool
    amount: float


class OrchestrateCharge(DataTransferObject):
    amount: float


class EchoContext(DataTransferObject):
    label: str


class EchoContextResponse(DataTransferObject):
    label: str
    correlation_id: str | None = None
    tenant: str | None = None


class SendBinaryPackage(DataTransferObject):
    payload: bytes


class SendBinaryPackageResponse(DataTransferObject):
    ok: bool


class RefuseCharge(DataTransferObject):
    reason: str


class LeakSecret(DataTransferObject):
    label: str


def rpc_details(error: grpc.RpcError) -> str:
    """The status details a failed call carried, never None in these cases."""
    details = error.details()  # pyright: ignore[reportAttributeAccessIssue]
    assert isinstance(details, str)
    return details


def _positive(value: int) -> int:
    if value < 0:
        raise ValueError("must be positive")
    return value


PositiveAmount = ValueObject(int, _positive, name="PositiveAmount")


class ChargeWithVO(DataTransferObject):
    amount: PositiveAmount


class ChargeWithVOResponse(DataTransferObject):
    amount: PositiveAmount


def _instance(name: str, with_app_service: bool = False) -> UseFramework:
    framework = UseFramework(name, log_after_execution=False)

    @framework.feature(ValidateCard)
    class ValidateCardFeature(Feature):
        """Validate a payment card."""

        def execute(self, dto: ValidateCard) -> ValidateCardResponse:
            return ValidateCardResponse(
                valid=len(dto.card_number) >= 4, card_number=dto.card_number
            )

    @framework.feature(ChargePayment)
    class ChargePaymentFeature(Feature):
        def execute(self, dto: ChargePayment) -> ChargePaymentResponse:
            return ChargePaymentResponse(charged=True, amount=dto.amount)

    @framework.feature(EchoContext)
    class EchoContextFeature(Feature):
        def execute(self, dto: EchoContext) -> EchoContextResponse:
            return EchoContextResponse(
                label=dto.label,
                correlation_id=self.context.get("correlation_id"),
                tenant=self.context.get("tenant"),
            )

    @framework.feature(SendBinaryPackage)
    class SendBinaryPackageFeature(Feature):
        def execute(self, dto: SendBinaryPackage) -> SendBinaryPackageResponse:
            return SendBinaryPackageResponse(ok=bool(dto.payload))

    @framework.feature(ChargeWithVO)
    class ChargeWithVOFeature(Feature):
        def execute(self, dto: ChargeWithVO) -> ChargeWithVOResponse:
            return ChargeWithVOResponse(amount=dto.amount)

    @framework.feature(RefuseCharge)
    class RefuseChargeFeature(Feature):
        def execute(self, dto: RefuseCharge) -> ChargePaymentResponse:
            raise ContractViolation(f"an invoice has to balance: {dto.reason}")

    @framework.feature(LeakSecret)
    class LeakSecretFeature(Feature):
        def execute(self, dto: LeakSecret) -> ChargePaymentResponse:
            raise RuntimeError("postgres://admin:hunter2@db:5432/prod")

    if with_app_service:

        @framework.app_service(OrchestrateCharge)
        class OrchestrateChargeService(ApplicationService):
            def execute(self, dto: OrchestrateCharge) -> ChargePaymentResponse:
                return self.feature_bus.execute(
                    ChargePayment(amount=dto.amount), ChargePaymentResponse
                )

    framework.build_root_bus()
    return framework


@pytest.fixture
def served() -> Iterator[GrpcClient]:
    """One gateway on an ephemeral port, with a client dialing it."""
    gateway = GrpcGateway({"qr": _instance("payment-qr", with_app_service=True)})
    server = gateway.server(max_workers=2)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    client = GrpcClient(f"127.0.0.1:{port}")
    try:
        yield client
    finally:
        client.close()
        server.stop(None)


def test_method_paths_are_package_service_dto():
    gateway = GrpcGateway(
        {"qr": _instance("a", with_app_service=True), "cybersource": _instance("b")}
    )
    paths = set(gateway.methods())

    assert "/qr.Features/ValidateCard" in paths
    assert "/qr.AppServices/OrchestrateCharge" in paths
    assert "/cybersource.Features/ValidateCard" in paths


def test_alias_with_hyphen_is_refused():
    """JSON-RPC accepts `bank-account`; a proto package does not."""
    with pytest.raises(ValueError):
        GrpcGateway({"bank-account": _instance("b")})


def test_layers_filter_and_exclude():
    framework = _instance("pay", with_app_service=True)
    apps_only = GrpcGateway({"pay": framework}, layers=("app_services",))
    excluded = GrpcGateway().add("pay", framework, exclude=[ChargePayment])

    assert set(apps_only.methods()) == {"/pay.AppServices/OrchestrateCharge"}
    assert "/pay.Features/ChargePayment" not in excluded.methods()
    assert "/pay.Features/ValidateCard" in excluded.methods()


def test_binary_dto_is_not_published():
    assert (
        "/pay.Features/SendBinaryPackage"
        not in GrpcGateway({"pay": _instance("p")}).methods()
    )


def test_unary_call_round_trip(served: GrpcClient):
    """Struct keys are data, not proto fields: `card_number` stays `card_number`,
    with none of the camelCase a generated message would impose on the DTO.
    """
    result = served.call("/qr.Features/ValidateCard", {"card_number": "4111", "cvv": "123"})

    assert result == {"valid": True, "card_number": "4111"}


def test_app_service_is_callable(served: GrpcClient):
    result = served.call("/qr.AppServices/OrchestrateCharge", {"amount": 5})

    assert result["charged"] is True
    assert result["amount"] == 5


def test_metadata_reaches_the_framework_context(served: GrpcClient):
    result = served.call(
        "/qr.Features/EchoContext",
        {"label": "ok"},
        context={"correlation_id": "req-9", "tenant": "acme"},
    )

    assert result["label"] == "ok"
    assert result["correlation_id"] == "req-9"
    assert result["tenant"] == "acme"


def test_context_from_metadata_reads_trace_and_prefixed_keys():
    merged = context_from_metadata(
        [
            ("x-correlation-id", "req-1"),
            ("traceparent", "00-abc-def-01"),
            ("sp-ctx-tenant", "acme"),
            ("user-agent", "grpc-python"),
            ("sp-ctx-blob-bin", b"ignored"),
        ]
    )

    assert merged == {
        "correlation_id": "req-1",
        "tenant": "acme",
        "carrier": {"traceparent": "00-abc-def-01"},
    }


def test_missing_field_is_invalid_argument(served: GrpcClient):
    with pytest.raises(grpc.RpcError) as error:
        served.call("/qr.Features/ValidateCard", {"card_number": "4111"})

    assert error.value.code() is grpc.StatusCode.INVALID_ARGUMENT
    assert json.loads(rpc_details(error.value))[0]["loc"] == ["cvv"]


def test_value_object_rejection_stays_serialisable(served: GrpcClient):
    """A ValueObject validate_fn raising ValueError puts the exception itself in
    ctx.error; the status details must survive json.dumps, not crash the handler.
    """
    with pytest.raises(grpc.RpcError) as error:
        served.call("/qr.Features/ChargeWithVO", {"amount": -5})

    assert error.value.code() is grpc.StatusCode.INVALID_ARGUMENT
    assert json.loads(rpc_details(error.value))


def test_domain_error_is_failed_precondition_with_its_message(served: GrpcClient):
    with pytest.raises(grpc.RpcError) as error:
        served.call("/qr.Features/RefuseCharge", {"reason": "line 3"})

    assert error.value.code() is grpc.StatusCode.FAILED_PRECONDITION
    assert "an invoice has to balance" in rpc_details(error.value)


def test_unexpected_error_never_reaches_the_caller(served: GrpcClient):
    with pytest.raises(grpc.RpcError) as error:
        served.call("/qr.Features/LeakSecret", {"label": "x"})

    assert error.value.code() is grpc.StatusCode.INTERNAL
    assert rpc_details(error.value) == "Internal error"


def test_unknown_method_is_unimplemented(served: GrpcClient):
    with pytest.raises(grpc.RpcError) as error:
        served.call("/qr.Features/DoesNotExist", {})

    assert error.value.code() is grpc.StatusCode.UNIMPLEMENTED


def test_describe_publishes_the_catalog_with_json_schema(served: GrpcClient):
    document = served.describe()
    services = {service["name"]: service for service in document["services"]}
    validate = next(
        method
        for method in services["qr.Features"]["methods"]
        if method["name"] == "ValidateCard"
    )

    assert set(services) == {"qr.Features", "qr.AppServices"}
    assert validate["path"] == "/qr.Features/ValidateCard"
    assert validate["description"] == "Validate a payment card."
    assert set(validate["params"]["properties"]) == {"card_number", "cvv"}
    assert DESCRIBE_PATH == "/sincpro.Introspection/Describe"


def test_proto_export_matches_what_is_served():
    gateway = GrpcGateway({"qr": _instance("payment-qr", with_app_service=True)})
    files = gateway.proto_files()
    source = files["qr.proto"]

    assert set(files) == {"qr.proto", "sincpro.proto"}
    assert "package qr;" in source
    assert 'import "google/protobuf/struct.proto";' in source
    assert "service Features {" in source
    assert "service AppServices {" in source
    assert (
        "  rpc ValidateCard(google.protobuf.Struct) returns (google.protobuf.Struct);"
        in source
    )
    assert "// Validate a payment card." in source


def test_write_proto_files(tmp_path):
    gateway = GrpcGateway({"qr": _instance("qr")})
    written = gateway.write_proto_files(tmp_path / "proto")

    assert {path.name for path in written} == {"qr.proto", "sincpro.proto"}
    assert (tmp_path / "proto" / "qr.proto").read_text().startswith("// Generated by")


def test_two_gateways_in_one_process_do_not_collide():
    """Descriptors go to a private pool; a shared one would refuse the second add."""
    first = GrpcGateway({"qr": _instance("a")}).server(reflection=True)
    second = GrpcGateway({"qr": _instance("b")}).server(reflection=True)

    assert first is not second


def test_server_reflection_lists_and_describes_the_services(served: GrpcClient):
    """What grpcurl reads: the catalog is discoverable without the .proto."""
    reflection_pb2 = pytest.importorskip("grpc_reflection.v1alpha.reflection_pb2")
    reflection_grpc = pytest.importorskip("grpc_reflection.v1alpha.reflection_pb2_grpc")
    stub = reflection_grpc.ServerReflectionStub(served.channel)

    def ask(**request) -> Any:
        requests = iter([reflection_pb2.ServerReflectionRequest(**request)])
        return next(iter(stub.ServerReflectionInfo(requests)))

    listed = ask(list_services="")
    names = {service.name for service in listed.list_services_response.service}
    described = ask(file_containing_symbol="qr.Features")

    assert {"qr.Features", "qr.AppServices", "sincpro.Introspection"} <= names
    assert described.file_descriptor_response.file_descriptor_proto
