"""A failure is described where it happened and reported once, by the outermost bus.

Each case is a shape consumers hit: a Feature failing inside a helper, an ApplicationService
over it, an error handler that maps the error (siat) or answers instead of raising, an
expected error, and one bounded context's bus called from another's Feature.
"""

from collections.abc import Mapping, Sequence
from typing import Any

import pytest
from structlog.testing import capture_logs

from sincpro_framework import ApplicationService, DataTransferObject, Feature, UseFramework
from sincpro_framework.observability import process


class CommandCharge(DataTransferObject):
    amount: int
    card: str


class CommandCheckout(DataTransferObject):
    order_id: int


class GatewayRejected(Exception):
    pass


class SdkError(Exception):
    pass


def _payments() -> UseFramework:
    framework = UseFramework(
        "payments-sdk", log_after_execution=False, hide_in_logs=["TOKEN"]
    )

    @framework.feature(CommandCharge)
    class Charge(Feature):
        def _call_gateway(self, dto: CommandCharge) -> None:
            raise GatewayRejected(f"card {dto.card} rejected")

        def execute(self, dto: CommandCharge) -> None:
            self._call_gateway(dto)

    @framework.app_service(CommandCheckout)
    class Checkout(ApplicationService):
        def execute(self, dto: CommandCheckout) -> None:
            self.feature_bus.execute(CommandCharge(amount=10, card="4111"))

    return framework


def _errors(logs: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [line for line in logs if line["log_level"] == "error"]


def _run(framework: UseFramework, dto: DataTransferObject, context: dict | None = None):
    with capture_logs() as logs:
        with pytest.raises(Exception) as raised:
            with framework.context(context or {}):
                framework(dto)
    return raised.value, logs


# ---------------------------------------------------------------------------------------------
# Where and what
# ---------------------------------------------------------------------------------------------


def test_the_error_line_names_the_handler_the_dto_and_the_line_that_failed():
    error, logs = _run(_payments(), CommandCharge(amount=10, card="4111"))

    [line] = _errors(logs)
    assert line["event"] == "Charge failed: GatewayRejected: card 4111 rejected"
    assert line["dto"] == "CommandCharge(amount=10, card='4111')"
    assert line["handler"] == "Charge"
    assert line["failed_in"] == "payments-sdk"
    assert line["error_type"] == "GatewayRejected"
    assert line["error_at"].endswith("in _payments.<locals>.Charge._call_gateway")
    assert line["exc_info"] is error


def test_the_context_is_on_the_error_line_except_the_hidden_keys():
    _, logs = _run(
        _payments(),
        CommandCharge(amount=10, card="4111"),
        {"correlation_id": "req-42", "TOKEN": "secret"},
    )

    [line] = _errors(logs)
    assert line["correlation_id"] == "req-42"
    assert "TOKEN" not in line


def test_a_feature_under_an_application_service_is_logged_once_with_its_chain():
    _, logs = _run(_payments(), CommandCheckout(order_id=7))

    [line] = _errors(logs)
    assert line["handler"] == "Charge"
    assert line["chain"] == "CommandCheckout → CommandCharge"
    assert line["dto"] == "CommandCharge(amount=10, card='4111')"


def test_the_exception_carries_a_note_saying_where_it_came_from():
    error, _ = _run(_payments(), CommandCheckout(order_id=7))

    [note] = error.__notes__
    assert note.startswith(
        "[sincpro] CommandCheckout → CommandCharge: Charge (payments-sdk) failed at "
    )
    assert "Charge._call_gateway" in note
    assert process.was_reported(error)


def test_an_error_raised_inside_a_library_names_both_places():
    """error_at is the consumer's last line; raised_at is where the library raised."""
    library: dict = {"__name__": "soap_library"}
    exec(
        compile("def send():\n    raise ConnectionError('SOAP fault')\n", "soap.py", "exec"),
        library,
    )

    framework = UseFramework("siat", log_after_execution=False)

    @framework.feature(CommandCharge)
    class SendInvoice(Feature):
        def execute(self, dto: CommandCharge) -> None:
            library["send"]()

    _, logs = _run(framework, CommandCharge(amount=1, card="x"))

    [line] = _errors(logs)
    assert "SendInvoice.execute" in line["error_at"]
    assert line["raised_at"] == "soap_library:2 in send"


# ---------------------------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------------------------


def test_a_handler_that_re_raises_still_leaves_one_error_line():
    """It used to leave none: the bus handed the error to the handler before logging it."""
    framework = _payments()

    def watch(error: Exception):
        raise error

    framework.add_global_error_handler(watch)

    _, logs = _run(framework, CommandCharge(amount=10, card="4111"))

    assert len(_errors(logs)) == 1


def test_a_mapped_error_leads_back_to_the_original_failure():
    """siat's shape: the handler turns the gateway error into the SDK's own exception."""
    framework = _payments()

    def to_sdk_error(error: Exception):
        raise SdkError(f"gateway: {error}") from error

    framework.add_global_error_handler(to_sdk_error)

    error, logs = _run(framework, CommandCharge(amount=10, card="4111"))

    [line] = _errors(logs)
    assert isinstance(error, SdkError)
    assert line["error_type"] == "GatewayRejected"
    assert line["raised_as"] == "SdkError"
    assert "Charge (payments-sdk) failed at" in error.__notes__[0]


def test_a_handler_that_answers_leaves_a_warning_and_no_error():
    framework = _payments()
    framework.add_feature_error_handler(lambda error: None)

    with capture_logs() as logs:
        framework(CommandCharge(amount=10, card="4111"))

    assert _errors(logs) == []
    [warning] = [line for line in logs if line["log_level"] == "warning"]
    assert warning["event"].endswith("(answered by an error handler)")
    assert warning["handler"] == "Charge"


def test_an_expected_error_is_logged_as_information_without_traceback():
    framework = _payments()
    framework.ignore_sentry_exceptions(GatewayRejected)

    _, logs = _run(framework, CommandCharge(amount=10, card="4111"))

    assert _errors(logs) == []
    [line] = [line for line in logs if line.get("handler") == "Charge"]
    assert line["log_level"] == "info"
    assert "exc_info" not in line


# ---------------------------------------------------------------------------------------------
# Across bounded contexts
# ---------------------------------------------------------------------------------------------


def test_a_bus_called_from_another_bus_feature_is_logged_once_by_the_outer_one():
    class CommandResolveTenant(DataTransferObject):
        pass

    class CommandCreateQuotation(DataTransferObject):
        pass

    common = UseFramework("common", log_after_execution=False)
    sales = UseFramework("sales", log_after_execution=False)
    sales.add_dependency("common", common)

    @common.feature(CommandResolveTenant)
    class ResolveTenant(Feature):
        def execute(self, dto: CommandResolveTenant) -> None:
            raise LookupError("tenant not found")

    @sales.feature(CommandCreateQuotation)
    class CreateQuotation(Feature):
        def execute(self, dto: CommandCreateQuotation) -> None:
            self.common(CommandResolveTenant())

    _, logs = _run(sales, CommandCreateQuotation())

    [line] = _errors(logs)
    assert line["app_name"] == "sales"
    assert line["failed_in"] == "common"
    assert line["handler"] == "ResolveTenant"
    assert line["chain"] == "CommandCreateQuotation → CommandResolveTenant"


def test_an_error_no_bus_saw_is_not_reported():
    assert not process.was_reported(RuntimeError("malformed request"))


# ---------------------------------------------------------------------------------------------
# Spans
# ---------------------------------------------------------------------------------------------


def test_the_exception_is_recorded_on_one_span_and_every_span_it_crosses_is_an_error(
    otel_setup,
):
    from opentelemetry.trace import StatusCode

    _run(_payments(), CommandCheckout(order_id=7))

    spans = {span.name: span for span in otel_setup.get_finished_spans()}
    exception_events = [
        event for span in spans.values() for event in span.events if event.name == "exception"
    ]
    assert len(exception_events) == 1
    assert (
        spans["CommandCharge"]
        .attributes["code.function.name"]
        .endswith("Charge._call_gateway")
    )
    assert spans["CommandCharge"].status.status_code == StatusCode.ERROR
    assert spans["CommandCheckout"].status.status_code == StatusCode.ERROR


def test_an_application_service_executing_an_unregistered_dto_is_told_so():
    class CommandNeverRegistered(DataTransferObject):
        pass

    framework = UseFramework("orchestration", log_after_execution=False)

    @framework.app_service(CommandCheckout)
    class Checkout(ApplicationService):
        def execute(self, dto: CommandCheckout) -> None:
            self.feature_bus.execute(CommandNeverRegistered())

    error, logs = _run(framework, CommandCheckout(order_id=1))

    assert str(error) == "CommandNeverRegistered is not registered as a feature"
    [line] = _errors(logs)
    assert line["handler"] == "Checkout"
