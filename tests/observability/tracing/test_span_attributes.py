"""What a use case says about itself lands on its own span — the one the bus opened, named
`context/Command` — and nothing it says can fail it (PRD_03 §3.1).

The incidents these guard: a NIT that lands on an adapter's HTTP span, so "the rejected invoices of
NIT X" finds nothing; a failed send without the NIT, precisely the one being audited; an
ApplicationService's attribute on its child Feature's span; a token written to a trace anyone with
Grafana reads; a typo in an attribute key that breaks the payment it was describing.
"""

from decimal import Decimal
from enum import StrEnum

import pytest

from sincpro_framework import (
    ApplicationService,
    DataTransferObject,
    Feature,
    ProgrammingError,
    UseFramework,
)
from sincpro_framework.observability import of, traces

pytest.importorskip("opentelemetry.sdk.trace")


class Environment(StrEnum):
    PRODUCTION = "production"
    TEST = "test"


class Rejected(Exception):
    pass


class CommandSendInvoice(DataTransferObject):
    nit: int | str
    branch_office: int
    environment: Environment
    xml: bytes = b""
    reject: bool = False


class ResponseSendInvoice(DataTransferObject):
    reception_code: str
    total: Decimal


def _siat() -> UseFramework:
    siat = UseFramework("siat-attrs", log_after_execution=False)

    @siat.feature(CommandSendInvoice)
    @traces.attributes(
        of(CommandSendInvoice).nit,
        of(CommandSendInvoice).environment,
        of(ResponseSendInvoice).reception_code,
        namespace="siat",
        branch=of(CommandSendInvoice).branch_office,
        amount=of(ResponseSendInvoice).total,
    )
    class SendInvoice(Feature):
        def execute(self, dto: CommandSendInvoice) -> ResponseSendInvoice:
            if dto.reject:
                raise Rejected("SIAT rejected the invoice")
            traces.annotate({"siat.cuf": "ABC123"})
            return ResponseSendInvoice(reception_code="R-77", total=Decimal("15.50"))

    return siat


def _span(exporter, name: str):
    [span] = [one for one in exporter.get_finished_spans() if one.name == name]
    return span


def test_declared_and_annotated_attributes_land_on_the_use_case_span(otel_setup):
    siat = _siat()

    siat(
        CommandSendInvoice(nit=1020304050, branch_office=2, environment=Environment.TEST),
        ResponseSendInvoice,
    )

    attributes = _span(otel_setup, "siat-attrs/CommandSendInvoice").attributes
    assert attributes["siat.nit"] == 1020304050
    assert attributes["siat.branch"] == 2
    assert attributes["siat.environment"] == "test"  # the Enum's value
    assert attributes["siat.reception_code"] == "R-77"
    assert attributes["siat.amount"] == 15.5
    assert attributes["siat.cuf"] == "ABC123"
    assert attributes["sincpro.outcome"] == "ok"  # the framework's own keys are untouched


def test_a_failed_run_still_carries_what_its_command_said(otel_setup):
    """The rejected invoice is the one being audited: its NIT must be on the span."""
    siat = _siat()

    with pytest.raises(Rejected):
        siat(
            CommandSendInvoice(
                nit="1020304050",
                branch_office=0,
                environment=Environment.PRODUCTION,
                reject=True,
            ),
            ResponseSendInvoice,
        )

    attributes = _span(otel_setup, "siat-attrs/CommandSendInvoice").attributes
    assert attributes["siat.nit"] == "1020304050"
    assert attributes["siat.branch"] == 0
    assert "siat.reception_code" not in attributes  # no Response, nothing invented


class CommandPay(DataTransferObject):
    merchant_id: str


class ResponsePay(DataTransferObject):
    transaction_id: str


class CommandCharge(DataTransferObject):
    merchant_id: str


class ResponseCharge(DataTransferObject):
    qr_id: int


def test_each_attribute_stays_on_the_span_of_the_use_case_that_said_it(otel_setup):
    """An ApplicationService and the Feature it runs each keep their own; an adapter's own span,
    active while it runs, is not where the use case's attribute lands."""
    from opentelemetry import trace

    payments = UseFramework("payments-attrs", log_after_execution=False)

    @payments.feature(CommandCharge)
    @traces.attributes(of(ResponseCharge).qr_id, namespace="payment")
    class Charge(Feature):
        def execute(self, dto: CommandCharge) -> ResponseCharge:
            with trace.get_tracer("bank-adapter").start_as_current_span("POST /qr"):
                traces.annotate({"payment.bank_reference": "BNB-9"})
            return ResponseCharge(qr_id=42)

    @payments.app_service(CommandPay)
    @traces.attributes(namespace="payment", merchant=of(CommandPay).merchant_id)
    class Pay(ApplicationService):
        def execute(self, dto: CommandPay) -> ResponsePay:
            charged = self.feature_bus.execute(
                CommandCharge(merchant_id=dto.merchant_id), ResponseCharge
            )
            traces.annotate({"payment.transaction_id": f"T-{charged.qr_id}"})
            return ResponsePay(transaction_id=f"T-{charged.qr_id}")

    payments(CommandPay(merchant_id="M-1"), ResponsePay)

    service = _span(otel_setup, "payments-attrs/CommandPay").attributes
    feature = _span(otel_setup, "payments-attrs/CommandCharge").attributes
    adapter = _span(otel_setup, "POST /qr").attributes
    assert (service["payment.merchant"], service["payment.transaction_id"]) == ("M-1", "T-42")
    assert "payment.qr_id" not in service and "payment.bank_reference" not in service
    assert (feature["payment.qr_id"], feature["payment.bank_reference"]) == (42, "BNB-9")
    assert "payment.merchant" not in feature and "payment.transaction_id" not in feature
    assert "payment.bank_reference" not in adapter


def test_any_key_goes_on_the_span_by_hand_and_a_value_it_cannot_hold_is_dropped(
    otel_setup,
):
    """What a service shows is its decision (PRD_03 §4.10): no key is refused for its name.
    Only a value a span cannot hold is dropped, and the use case runs."""
    shop = UseFramework("shop-attrs", log_after_execution=False)

    class CommandBuy(DataTransferObject):
        pass

    @shop.feature(CommandBuy)
    class Buy(Feature):
        def execute(self, dto: CommandBuy) -> None:
            traces.annotate(
                {
                    "shop.order_id": "O-1",
                    "shop.access_token": "eyJ...",
                    "nit": "1",
                    "shop.payload": {
                        "a": 1
                    },  # pyright: ignore[reportArgumentType] — not a value a span holds
                }
            )

    shop(CommandBuy())

    attributes = _span(otel_setup, "shop-attrs/CommandBuy").attributes
    assert attributes["shop.order_id"] == "O-1"
    assert (attributes["shop.access_token"], attributes["nit"]) == ("eyJ...", "1")
    assert "shop.payload" not in attributes


def test_annotate_outside_a_use_case_does_nothing(otel_setup):
    traces.annotate({"shop.order_id": "O-1"})

    assert otel_setup.get_finished_spans() == ()


class CommandLogin(DataTransferObject):
    user_email: str
    api_key: str
    payload: dict
    document: bytes
    code: str


@pytest.mark.parametrize(
    ("declare", "refusal"),
    [
        (lambda: traces.attributes(of(CommandLogin).payload, namespace="auth"), "is dict"),
        (lambda: traces.attributes(of(CommandLogin).document, namespace="auth"), "is bytes"),
        (lambda: traces.attributes(namespace="auth"), "nothing declared"),
        (lambda: traces.attributes(of(CommandLogin).code, namespace=""), "namespace"),
    ],
    ids=["a mapping", "bytes", "empty", "no namespace"],
)
def test_a_declaration_that_could_never_work_is_refused_at_import(declare, refusal):
    with pytest.raises(ProgrammingError, match=refusal):
        declare()


@pytest.mark.parametrize(
    "declare",
    [
        lambda: traces.attributes(of(CommandLogin).user_email, namespace="auth"),
        lambda: traces.attributes(of(CommandLogin).api_key, namespace="auth"),
        lambda: traces.attributes(namespace="auth", secret=of(CommandLogin).code),
        lambda: traces.attributes(of(CommandLogin).code, namespace="sincpro"),
        lambda: traces.attributes(of(CommandLogin).code, namespace="http"),
        lambda: traces.attributes(of(CommandLogin).code, namespace="Auth"),
    ],
    ids=[
        "personal data",
        "credential",
        "named secret",
        "the framework's namespace",
        "an OTel convention",
        "not lowercase",
    ],
)
def test_no_key_is_refused_for_its_name(declare):
    """Nothing is refused for its content (PRD_03 §4.10): the declaration stands."""
    assert callable(declare())


def test_a_path_into_another_dto_or_a_key_declared_twice_is_refused_at_import():
    with pytest.raises(ProgrammingError, match="neither the Command nor the Response"):

        @traces.attributes(of(CommandLogin).code, namespace="pay")
        class Foreign(Feature):
            def execute(self, dto: CommandPay) -> ResponsePay: ...

    with pytest.raises(ProgrammingError, match="declared twice"):

        @traces.attributes(of(CommandPay).merchant_id, namespace="pay")
        @traces.attributes(namespace="pay", merchant_id=of(CommandPay).merchant_id)
        class Twice(Feature):
            def execute(self, dto: CommandPay) -> ResponsePay: ...
