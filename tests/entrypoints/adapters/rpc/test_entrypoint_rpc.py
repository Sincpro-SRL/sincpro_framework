"""entrypoint_rpc: JSON-RPC 2.0 methods `{namespace}.{operation}`, context, OpenRPC discover.

These buses publish their whole catalog (`Exposure.CATALOG`, `unguarded=True`): what is
protected here is the protocol and the naming, the same in either mode; declared exposure has
its own tests (test_jsonrpc_naming.py)."""

import json
from typing import Any

from sincpro_framework import (
    ApplicationService,
    DataTransferObject,
    Feature,
    UseFramework,
)
from sincpro_framework.ddd import ValueObject
from sincpro_framework.ddd.exceptions import DomainError
from sincpro_framework.entrypoints import Exposure
from sincpro_framework.entrypoints.adapters.rpc import RpcGateway
from sincpro_framework.entrypoints.adapters.rpc.errors import INVALID_PARAMS, METHOD_NOT_FOUND
from sincpro_framework.entrypoints.adapters.rpc.jrpc import DISCOVER_METHOD
from sincpro_framework.entrypoints.entrypoint.gateway import Buses
from sincpro_framework.entrypoints.infrastructure.http import merge_http_context


def catalog_gateway(instances: Buses | None = None, **options: Any) -> RpcGateway:
    return RpcGateway(instances, exposure=Exposure.CATALOG, unguarded=True, **options)


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
                label=dto.label, correlation_id=self.context.get("correlation_id")
            )

    @framework.feature(SendBinaryPackage)
    class SendBinaryPackageFeature(Feature):
        def execute(self, dto: SendBinaryPackage) -> SendBinaryPackageResponse:
            return SendBinaryPackageResponse(ok=bool(dto.payload))

    @framework.feature(ChargeWithVO)
    class ChargeWithVOFeature(Feature):
        def execute(self, dto: ChargeWithVO) -> ChargeWithVOResponse:
            return ChargeWithVOResponse(amount=dto.amount)

    if with_app_service:

        @framework.app_service(OrchestrateCharge)
        class OrchestrateChargeService(ApplicationService):
            def execute(self, dto: OrchestrateCharge) -> ChargePaymentResponse:
                return self.feature_bus.execute(
                    ChargePayment(amount=dto.amount), ChargePaymentResponse
                )

    framework.build_root_bus()
    return framework


def test_method_names_are_namespace_and_operation_without_the_layer():
    gateway = catalog_gateway({"qr": _instance("payment-qr"), "cybersource": _instance("cs")})
    names = set(gateway.methods())

    assert "qr.validate_card" in names
    assert "cybersource.validate_card" in names
    assert "qr.charge_payment" in names
    assert not any("features" in name for name in names)


def test_two_instances_same_dto_do_not_collide():
    gateway = catalog_gateway({"qr": _instance("a"), "bank_account": _instance("b")})
    qr = rpc_object(
        gateway.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "qr.charge_payment",
                "params": {"amount": 10},
            }
        )
    )
    bank = rpc_object(
        gateway.handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "bank_account.charge_payment",
                "params": {"amount": 99},
            }
        )
    )

    assert qr["result"]["amount"] == 10
    assert bank["result"]["amount"] == 99


def test_app_service_method_and_layers_filter():
    framework = _instance("pay", with_app_service=True)
    all_layers = catalog_gateway({"pay": framework})
    apps_only = catalog_gateway({"pay": framework}, layers=("app_services",))

    assert "pay.orchestrate_charge" in all_layers.methods()
    assert "pay.validate_card" in all_layers.methods()
    assert set(apps_only.methods()) == {"pay.orchestrate_charge"}

    reply = rpc_object(
        all_layers.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "pay.orchestrate_charge",
                "params": {"amount": 5},
            }
        )
    )
    assert reply["result"]["charged"] is True


def test_context_reaches_feature():
    gateway = catalog_gateway({"pay": _instance("ctx")})
    reply = rpc_object(
        gateway.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "pay.echo_context",
                "params": {"label": "ok"},
                "context": {"correlation_id": "req-9"},
            }
        )
    )

    assert reply["result"] == {"label": "ok", "correlation_id": "req-9"}


def test_inherited_http_context_is_used_when_body_omits_it():
    gateway = catalog_gateway({"pay": _instance("ctx")})
    reply = rpc_object(
        gateway.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "pay.echo_context",
                "params": {"label": "hdr"},
            },
            context={"correlation_id": "from-header"},
        )
    )

    assert reply["result"]["correlation_id"] == "from-header"


def test_invalid_params_is_32602():
    gateway = catalog_gateway({"pay": _instance("pay")})
    reply = rpc_object(
        gateway.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "pay.validate_card",
                "params": {"card_number": "4111"},
            }
        )
    )

    assert reply["error"]["code"] == INVALID_PARAMS


def test_value_object_rejection_stays_json_safe():
    """A ValueObject validate_fn raising ValueError puts the exception itself in
    ctx.error (pydantic ValidationError.errors()). The -32602 envelope must not
    leak that raw exception, or json.dumps on the reply would crash the host.
    """
    gateway = catalog_gateway({"pay": _instance("pay")})
    reply = rpc_object(
        gateway.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "pay.charge_with_vo",
                "params": {"amount": -5},
            }
        )
    )

    assert reply["error"]["code"] == INVALID_PARAMS
    assert json.dumps(reply)


def test_unknown_method_is_32601():
    gateway = catalog_gateway({"pay": _instance("pay")})
    reply = rpc_object(
        gateway.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "pay.does_not_exist", "params": {}}
        )
    )

    assert reply["error"]["code"] == METHOD_NOT_FOUND


def test_binary_dto_is_not_published():
    names = set(catalog_gateway({"pay": _instance("pay")}).methods())
    assert "pay.send_binary_package" not in names


def test_per_instance_exclude_uses_shared_catalog():
    framework = _instance("pay")
    gateway = catalog_gateway().add("pay", framework, exclude=[ChargePayment])
    names = set(gateway.methods())

    assert "pay.validate_card" in names
    assert "pay.charge_payment" not in names


def test_rpc_discover_lists_catalog_and_openrpc_version():
    document = catalog_gateway({"qr": _instance("qr", with_app_service=True)}).discover()
    names = {method["name"] for method in document["methods"]}

    assert document["openrpc"] == "1.4.0"
    assert DISCOVER_METHOD in names
    assert "qr.validate_card" in names
    assert "qr.orchestrate_charge" in names


def test_batch_and_notification():
    gateway = catalog_gateway({"pay": _instance("pay")})
    batch = rpc_batch(
        gateway.handle(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "pay.charge_payment",
                    "params": {"amount": 1},
                },
                {
                    "jsonrpc": "2.0",
                    "method": "pay.charge_payment",
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
                "method": "pay.charge_payment",
                "params": {"amount": 3},
            }
        )
        is None
    )


def test_build_rpc_app_requires_extra_or_returns_asgi():
    from sincpro_framework.entrypoints.adapters.rpc import build_rpc_app

    try:
        app = build_rpc_app({"pay": _instance("pay")})
        assert app is not None
    except ImportError as error:
        assert "sincpro-framework[rpc]" in str(error)


# ---------------------------------------------------------------------------
# HTTP transport — headers folded into the framework context
# ---------------------------------------------------------------------------


def test_correlation_id_header_reaches_the_feature():
    """X-Correlation-Id is what ties an RPC call to the caller's request log."""
    gateway = catalog_gateway({"pay": _instance("ctx")})
    header_context = merge_http_context({"x-correlation-id": "req-from-gateway"})

    reply = rpc_object(
        gateway.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "pay.echo_context",
                "params": {"label": "ok"},
            },
            context=header_context,
        )
    )

    assert reply["result"]["correlation_id"] == "req-from-gateway"


def test_traceparent_header_becomes_the_otel_carrier():
    """W3C traceparent must arrive as a carrier so the span adopts the remote parent."""
    traceparent = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"

    merged = merge_http_context({"traceparent": traceparent})

    assert merged == {"carrier": {"traceparent": traceparent}}


def test_body_context_wins_over_the_headers():
    """Headers are a default: an explicit context in the payload overrides them."""
    gateway = catalog_gateway({"pay": _instance("ctx")})
    header_context = merge_http_context({"x-correlation-id": "from-header"})

    reply = rpc_object(
        gateway.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "pay.echo_context",
                "params": {"label": "ok"},
                "context": {"correlation_id": "from-body"},
            },
            context=header_context,
        )
    )

    assert reply["result"]["correlation_id"] == "from-body"


def test_request_without_context_headers_inherits_nothing():
    """No headers must not fabricate a context the DTO would have to carry."""
    assert merge_http_context({}) == {}
    assert merge_http_context({"content-type": "application/json"}) == {}


# --- what a failure is allowed to tell the caller -------------------------------------------


def _blowing_up(error: Exception) -> UseFramework:
    framework = UseFramework(f"boom-{id(error)}", log_after_execution=False)

    @framework.feature(ChargePayment)
    class Explodes(Feature):
        def execute(self, dto: ChargePayment) -> ChargePaymentResponse:
            raise error

    return framework


def _asked(framework: UseFramework) -> Any:
    return catalog_gateway({"pay": framework}).handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "pay.charge_payment",
            "params": {"amount": 10},
        }
    )


def test_an_internal_failure_tells_the_caller_nothing_about_the_inside():
    """**A credential leak, until it was not.** The exception's own text used to be sent as the
    error's `data`, and a database error's text carries a connection string with its password
    and the statement with the value it was filtering on. The log still gets all of it."""
    from sqlalchemy.exc import OperationalError

    answered = _asked(
        _blowing_up(
            OperationalError(
                "SELECT * FROM users WHERE token = 'secret-123'",
                {},
                Exception("postgres://admin:hunter2@10.0.0.5/prod refused"),
            )
        )
    )

    assert answered["error"]["code"] == -32603
    assert answered["error"]["message"] == "Internal error"
    assert answered["error"]["data"] == {
        "kind": "internal",
        "reason": "INTERNAL_ERROR",
        "retryable": True,
    }
    assert "hunter2" not in str(answered)
    assert "secret-123" not in str(answered)


def test_a_domain_error_still_answers_the_caller():
    """The other half: a `DomainError` was written for whoever asked, and hiding it helps
    nobody."""

    answered = _asked(_blowing_up(DomainError("an invoice has to balance")))

    assert answered["error"]["data"]["message"] == "an invoice has to balance"


def test_a_failure_the_bus_logged_is_not_logged_again_by_the_transport():
    from structlog.testing import capture_logs

    with capture_logs() as logs:
        _asked(_blowing_up(RuntimeError("gateway down")))

    errors = [line for line in logs if line["log_level"] == "error"]
    assert [line["event"] for line in errors] == [
        "Explodes failed: RuntimeError: gateway down"
    ]
