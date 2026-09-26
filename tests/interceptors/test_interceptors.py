"""Interceptors: code that runs around one use case, from outside it.

The contract a consumer builds on: an interceptor sees the Command and the response, may veto,
adjust either, or answer by itself — and never changes what class either of them is.
"""

from collections.abc import Mapping, Sequence
from typing import Any

import pytest
from structlog.testing import capture_logs

from sincpro_framework import (
    ApplicationService,
    CallNext,
    DataTransferObject,
    Feature,
    UseFramework,
)
from sincpro_framework.exceptions import (
    BusAlreadyBuilt,
    InterceptorContractViolation,
    UnknownDTOToExecute,
)
from sincpro_framework.introspection import features


class CommandCreateInvoice(DataTransferObject):
    customer_id: str
    due_days: int = 0


class ResponseCreateInvoice(DataTransferObject):
    customer_id: str
    due_days: int
    note: str = ""


class CommandCheckout(DataTransferObject):
    customer_id: str


class ResponseCheckout(DataTransferObject):
    invoice: ResponseCreateInvoice


class SomethingElse(DataTransferObject):
    pass


class NoCredit(Exception):
    pass


def _billing(handled: list[str] | None = None) -> UseFramework:
    billing = UseFramework("billing", log_after_execution=False)

    @billing.feature(CommandCreateInvoice)
    class CreateInvoice(Feature):
        def execute(self, dto: CommandCreateInvoice) -> ResponseCreateInvoice:
            if handled is not None:
                handled.append(dto.customer_id)
            return ResponseCreateInvoice(customer_id=dto.customer_id, due_days=dto.due_days)

    @billing.app_service(CommandCheckout)
    class Checkout(ApplicationService):
        def execute(self, dto: CommandCheckout) -> ResponseCheckout:
            invoice = self.feature_bus.execute(
                CommandCreateInvoice(customer_id=dto.customer_id), ResponseCreateInvoice
            )
            assert invoice is not None
            return ResponseCheckout(invoice=invoice)

    return billing


def _create(billing: UseFramework, customer_id: str = "acme") -> ResponseCreateInvoice:
    response = billing(CommandCreateInvoice(customer_id=customer_id), ResponseCreateInvoice)
    assert response is not None
    return response


# ---------------------------------------------------------------------------------------------
# What an interceptor can do
# ---------------------------------------------------------------------------------------------


def test_an_interceptor_sees_the_command_and_adjusts_the_response():
    billing = _billing()
    seen: list[str] = []

    @billing.interceptor(CommandCreateInvoice)
    def annotate(
        dto: CommandCreateInvoice, call_next: CallNext[ResponseCreateInvoice]
    ) -> ResponseCreateInvoice:
        seen.append(dto.customer_id)
        return call_next(dto).model_copy(update={"note": "checked"})

    assert _create(billing).note == "checked"
    assert seen == ["acme"]


def test_an_interceptor_adjusts_the_command_the_handler_receives():
    billing = _billing()

    @billing.interceptor(CommandCreateInvoice)
    def thirty_days(
        dto: CommandCreateInvoice, call_next: CallNext[ResponseCreateInvoice]
    ) -> ResponseCreateInvoice:
        return call_next(dto.model_copy(update={"due_days": 30}))

    assert _create(billing).due_days == 30


def test_an_interceptor_that_raises_vetoes_and_the_handler_never_runs():
    handled: list[str] = []
    billing = _billing(handled)

    @billing.interceptor(CommandCreateInvoice)
    def credit_check(
        dto: CommandCreateInvoice, call_next: CallNext[ResponseCreateInvoice]
    ) -> ResponseCreateInvoice:
        raise NoCredit(dto.customer_id)

    with pytest.raises(NoCredit):
        _create(billing)
    assert handled == []


def test_an_interceptor_that_does_not_call_next_answers_by_itself():
    handled: list[str] = []
    billing = _billing(handled)

    @billing.interceptor(CommandCreateInvoice)
    def cached(
        dto: CommandCreateInvoice, call_next: CallNext[ResponseCreateInvoice]
    ) -> ResponseCreateInvoice:
        return ResponseCreateInvoice(customer_id=dto.customer_id, due_days=0, note="cached")

    assert _create(billing).note == "cached"
    assert handled == []


# ---------------------------------------------------------------------------------------------
# What an interceptor cannot do
# ---------------------------------------------------------------------------------------------


def test_passing_another_command_class_on_is_refused():
    billing = _billing()

    @billing.interceptor(CommandCreateInvoice)
    def swaps_the_command(dto: CommandCreateInvoice, call_next: CallNext[Any]) -> Any:
        return call_next(SomethingElse())

    with pytest.raises(
        InterceptorContractViolation, match="swaps_the_command.*SomethingElse"
    ):
        _create(billing)


def test_answering_with_another_response_class_is_refused():
    billing = _billing()

    @billing.interceptor(CommandCreateInvoice)
    def swaps_the_response(dto: CommandCreateInvoice, call_next: CallNext[Any]) -> Any:
        call_next(dto)
        return SomethingElse()

    with pytest.raises(
        InterceptorContractViolation, match="swaps_the_response.*SomethingElse"
    ):
        _create(billing)


# ---------------------------------------------------------------------------------------------
# Where and in which order it runs
# ---------------------------------------------------------------------------------------------


def test_an_interceptor_runs_when_an_application_service_executes_the_command():
    billing = _billing()

    @billing.interceptor(CommandCreateInvoice)
    def annotate(
        dto: CommandCreateInvoice, call_next: CallNext[ResponseCreateInvoice]
    ) -> ResponseCreateInvoice:
        return call_next(dto).model_copy(update={"note": "checked"})

    response = billing(CommandCheckout(customer_id="acme"), ResponseCheckout)

    assert response is not None
    assert response.invoice.note == "checked"


def test_interceptors_run_in_registration_order_outermost_first():
    billing = _billing()
    trail: list[str] = []

    def recording(name: str):
        def interceptor(dto: Any, call_next: CallNext[Any]) -> Any:
            trail.append(f"{name}:before")
            response = call_next(dto)
            trail.append(f"{name}:after")
            return response

        return interceptor

    billing.interceptor(CommandCreateInvoice)(recording("outer"))
    billing.interceptor(CommandCreateInvoice)(recording("inner"))

    _create(billing)

    assert trail == ["outer:before", "inner:before", "inner:after", "outer:after"]


def test_an_interceptor_for_every_command_wraps_each_execution():
    billing = _billing()
    commands: list[str] = []

    @billing.interceptor()
    def every_command(dto: Any, call_next: CallNext[Any]) -> Any:
        commands.append(type(dto).__name__)
        return call_next(dto)

    billing(CommandCheckout(customer_id="acme"), ResponseCheckout)

    assert commands == ["CommandCheckout", "CommandCreateInvoice"]


def test_one_interceptor_can_name_several_commands():
    billing = _billing()
    commands: list[str] = []

    @billing.interceptor(CommandCreateInvoice, CommandCheckout)
    def both(dto: Any, call_next: CallNext[Any]) -> Any:
        commands.append(type(dto).__name__)
        return call_next(dto)

    billing(CommandCheckout(customer_id="acme"), ResponseCheckout)

    assert commands == ["CommandCheckout", "CommandCreateInvoice"]


# ---------------------------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------------------------


def test_registering_after_the_bus_was_built_is_refused():
    billing = _billing()
    _create(billing)

    with pytest.raises(BusAlreadyBuilt, match="late"):

        @billing.interceptor(CommandCreateInvoice)
        def late(dto: Any, call_next: CallNext[Any]) -> Any:
            return call_next(dto)


def test_naming_a_command_nobody_answers_is_refused_when_the_bus_is_built():
    billing = _billing()

    @billing.interceptor(SomethingElse)
    def lost(dto: Any, call_next: CallNext[Any]) -> Any:
        return call_next(dto)

    with pytest.raises(UnknownDTOToExecute, match="lost.*SomethingElse"):
        billing.build_root_bus()


# ---------------------------------------------------------------------------------------------
# Observability and introspection
# ---------------------------------------------------------------------------------------------


def _errors(logs: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [line for line in logs if line["log_level"] == "error"]


def test_a_failure_inside_an_interceptor_is_reported_on_the_interceptors_line():
    billing = _billing()

    @billing.interceptor(CommandCreateInvoice)
    def credit_check(dto: Any, call_next: CallNext[Any]) -> Any:
        raise NoCredit(dto.customer_id)

    with capture_logs() as logs:
        with pytest.raises(NoCredit):
            _create(billing)

    [line] = _errors(logs)
    assert "credit_check" in line["error_at"]


def test_introspection_lists_the_interceptors_of_each_command_in_order():
    billing = _billing()

    @billing.interceptor()
    def audit(dto: Any, call_next: CallNext[Any]) -> Any:
        return call_next(dto)

    @billing.interceptor(CommandCreateInvoice)
    def credit_check(dto: Any, call_next: CallNext[Any]) -> Any:
        return call_next(dto)

    billing.build_root_bus()
    described = features(billing)["CommandCreateInvoice"]

    assert [name.rsplit(".", 1)[-1] for name in described.interceptors] == [
        "audit",
        "credit_check",
    ]
