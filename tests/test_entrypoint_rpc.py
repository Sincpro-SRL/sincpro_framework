"""entrypoint_rpc: JSON-RPC 2.0 methods instance.layer.Dto, context, OpenRPC discover."""

from typing import Any

from sincpro_framework import ApplicationService, DataTransferObject, Feature, UseFramework
from sincpro_framework.entrypoints.rpc import RpcGateway
from sincpro_framework.entrypoints.rpc.protocol import (
    DISCOVER_METHOD,
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
)


def rpc_object(payload: dict[str, Any] | list[Any] | None) -> dict[str, Any]:
    assert isinstance(payload, dict)
    return payload


def rpc_batch(payload: dict[str, Any] | list[Any] | None) -> list[Any]:
    assert isinstance(payload, list)
    return payload


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


class SendBinaryPackage(DataTransferObject):
    payload: bytes


class SendBinaryPackageResponse(DataTransferObject):
    ok: bool


def _instance(name: str, *, with_app_service: bool = False) -> UseFramework:
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
                label=dto.label, correlation_id=self.context.get("correlation_id")
            )

    @framework.feature(SendBinaryPackage)
    class SendBinaryPackageFeature(Feature):
        def execute(self, dto: SendBinaryPackage) -> SendBinaryPackageResponse:
            return SendBinaryPackageResponse(ok=bool(dto.payload))

    if with_app_service:

        @framework.app_service(OrchestrateCharge)
        class OrchestrateChargeService(ApplicationService):
            def execute(self, dto: OrchestrateCharge) -> ChargePaymentResponse:
                return self.feature_bus.execute(
                    ChargePayment(amount=dto.amount), ChargePaymentResponse
                )

    framework.build_root_bus()
    return framework


def test_method_names_are_instance_layer_dto():
    gateway = RpcGateway({"qr": _instance("payment-qr"), "cybersource": _instance("cs")})
    names = set(gateway.methods())

    assert "qr.features.ValidateCard" in names
    assert "cybersource.features.ValidateCard" in names
    assert "qr.features.ChargePayment" in names


def test_two_instances_same_dto_do_not_collide():
    gateway = RpcGateway({"qr": _instance("a"), "bank_account": _instance("b")})
    qr = rpc_object(
        gateway.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "qr.features.ChargePayment",
                "params": {"amount": 10},
            }
        )
    )
    bank = rpc_object(
        gateway.handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "bank_account.features.ChargePayment",
                "params": {"amount": 99},
            }
        )
    )

    assert qr["result"]["amount"] == 10
    assert bank["result"]["amount"] == 99


def test_app_service_method_and_layers_filter():
    framework = _instance("pay", with_app_service=True)
    all_layers = RpcGateway({"pay": framework})
    apps_only = RpcGateway({"pay": framework}, layers=("app_services",))

    assert "pay.app_services.OrchestrateCharge" in all_layers.methods()
    assert "pay.features.ValidateCard" in all_layers.methods()
    assert set(apps_only.methods()) == {"pay.app_services.OrchestrateCharge"}

    reply = rpc_object(
        all_layers.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "pay.app_services.OrchestrateCharge",
                "params": {"amount": 5},
            }
        )
    )
    assert reply["result"]["charged"] is True


def test_context_reaches_feature():
    gateway = RpcGateway({"pay": _instance("ctx")})
    reply = rpc_object(
        gateway.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "pay.features.EchoContext",
                "params": {"label": "ok"},
                "context": {"correlation_id": "req-9"},
            }
        )
    )

    assert reply["result"] == {"label": "ok", "correlation_id": "req-9"}


def test_inherited_http_context_is_used_when_body_omits_it():
    gateway = RpcGateway({"pay": _instance("ctx")})
    reply = rpc_object(
        gateway.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "pay.features.EchoContext",
                "params": {"label": "hdr"},
            },
            context={"correlation_id": "from-header"},
        )
    )

    assert reply["result"]["correlation_id"] == "from-header"


def test_invalid_params_is_32602():
    gateway = RpcGateway({"pay": _instance("pay")})
    reply = rpc_object(
        gateway.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "pay.features.ValidateCard",
                "params": {"card_number": "4111"},
            }
        )
    )

    assert reply["error"]["code"] == INVALID_PARAMS


def test_unknown_method_is_32601():
    gateway = RpcGateway({"pay": _instance("pay")})
    reply = rpc_object(
        gateway.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "pay.features.DoesNotExist", "params": {}}
        )
    )

    assert reply["error"]["code"] == METHOD_NOT_FOUND


def test_binary_dto_is_not_published():
    names = set(RpcGateway({"pay": _instance("pay")}).methods())
    assert "pay.features.SendBinaryPackage" not in names


def test_per_instance_exclude_uses_shared_catalog():
    framework = _instance("pay")
    gateway = RpcGateway().add("pay", framework, exclude=[ChargePayment])
    names = set(gateway.methods())

    assert "pay.features.ValidateCard" in names
    assert "pay.features.ChargePayment" not in names


def test_rpc_discover_lists_catalog_and_openrpc_version():
    document = RpcGateway({"qr": _instance("qr", with_app_service=True)}).discover()
    names = {method["name"] for method in document["methods"]}

    assert document["openrpc"] == "1.4.0"
    assert DISCOVER_METHOD in names
    assert "qr.features.ValidateCard" in names
    assert "qr.app_services.OrchestrateCharge" in names


def test_batch_and_notification():
    gateway = RpcGateway({"pay": _instance("pay")})
    batch = rpc_batch(
        gateway.handle(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "pay.features.ChargePayment",
                    "params": {"amount": 1},
                },
                {
                    "jsonrpc": "2.0",
                    "method": "pay.features.ChargePayment",
                    "params": {"amount": 2},
                },
            ]
        )
    )

    assert len(batch) == 1
    assert batch[0]["result"]["amount"] == 1
    assert (
        gateway.handle(
            {
                "jsonrpc": "2.0",
                "method": "pay.features.ChargePayment",
                "params": {"amount": 3},
            }
        )
        is None
    )


def test_build_rpc_app_requires_extra_or_returns_asgi():
    from sincpro_framework.entrypoints.rpc import build_rpc_app

    try:
        app = build_rpc_app({"pay": _instance("pay")})
        assert app is not None
    except ImportError as error:
        assert "sincpro-framework[rpc]" in str(error)
