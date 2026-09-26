"""`replaces=`: another handler answers instead of the core's — explicitly, and visibly.

The core's use case is closed to modification. An addon that must change it for good registers
its own handler naming the one it replaces; the core handler is skipped, and building the bus,
the span and introspection all say who replaced whom.
"""

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
from sincpro_framework.exceptions import DTOAlreadyRegistered, UnknownDTOToExecute
from sincpro_framework.introspection import app_services, describe, features


class CommandComputeTax(DataTransferObject):
    amount: int


class ResponseComputeTax(DataTransferObject):
    tax: int


class ResponseComputeTaxWithBreakdown(ResponseComputeTax):
    breakdown: str = ""


class UnrelatedResponse(DataTransferObject):
    pass


class CommandCheckout(DataTransferObject):
    amount: int


def _billing() -> tuple[UseFramework, type[Feature]]:
    billing = UseFramework("billing", log_after_execution=False)

    @billing.feature(CommandComputeTax)
    class ComputeTax(Feature):
        def execute(self, dto: CommandComputeTax) -> ResponseComputeTax:
            return ResponseComputeTax(tax=dto.amount * 13 // 100)

    return billing, ComputeTax


def _tax(billing: UseFramework) -> ResponseComputeTax:
    answer = billing(CommandComputeTax(amount=100), ResponseComputeTax)
    assert answer is not None
    return answer


def test_the_replacement_answers_and_the_core_handler_does_not_run():
    billing, ComputeTax = _billing()

    @billing.feature(CommandComputeTax, replaces=ComputeTax)
    class ComputeTaxBolivia(Feature):
        def execute(self, dto: CommandComputeTax) -> ResponseComputeTax:
            return ResponseComputeTax(tax=dto.amount * 16 // 100)

    assert _tax(billing).tax == 16


def test_a_replacement_may_answer_a_narrower_response():
    billing, ComputeTax = _billing()

    @billing.feature(CommandComputeTax, replaces=ComputeTax)
    class Detailed(Feature):
        def execute(self, dto: CommandComputeTax) -> ResponseComputeTaxWithBreakdown:
            return ResponseComputeTaxWithBreakdown(tax=13, breakdown="IVA 13%")

    assert isinstance(_tax(billing), ResponseComputeTaxWithBreakdown)


def test_interceptors_wrap_the_replacement():
    billing, ComputeTax = _billing()

    @billing.feature(CommandComputeTax, replaces=ComputeTax)
    class ComputeTaxBolivia(Feature):
        def execute(self, dto: CommandComputeTax) -> ResponseComputeTax:
            return ResponseComputeTax(tax=16)

    @billing.interceptor(CommandComputeTax)
    def doubled(dto: Any, call_next: CallNext[ResponseComputeTax]) -> ResponseComputeTax:
        return call_next(dto).model_copy(update={"tax": 32})

    assert _tax(billing).tax == 32


def test_a_replacement_can_itself_be_replaced_by_naming_it():
    billing, ComputeTax = _billing()

    @billing.feature(CommandComputeTax, replaces=ComputeTax)
    class Bolivia(Feature):
        def execute(self, dto: CommandComputeTax) -> ResponseComputeTax:
            return ResponseComputeTax(tax=16)

    @billing.feature(CommandComputeTax, replaces=Bolivia)
    class Promo(Feature):
        def execute(self, dto: CommandComputeTax) -> ResponseComputeTax:
            return ResponseComputeTax(tax=0)

    assert _tax(billing).tax == 0
    assert [
        name.rsplit(".", 1)[-1] for name in describe(billing, CommandComputeTax).replaces
    ] == [
        "ComputeTax",
        "Bolivia",
    ]


# ---------------------------------------------------------------------------------------------
# Refused
# ---------------------------------------------------------------------------------------------


def test_replacing_a_class_that_is_not_the_one_registered_is_refused():
    billing, _ = _billing()

    class NotRegistered(Feature):
        def execute(self, dto: Any) -> None:
            return None

    with pytest.raises(
        DTOAlreadyRegistered, match="Wrong replaces NotRegistered.*ComputeTax"
    ):

        @billing.feature(CommandComputeTax, replaces=NotRegistered)
        class Wrong(Feature):
            def execute(self, dto: Any) -> None:
                return None


def test_a_second_replacement_of_the_same_handler_names_the_first():
    billing, ComputeTax = _billing()

    @billing.feature(CommandComputeTax, replaces=ComputeTax)
    class Bolivia(Feature):
        def execute(self, dto: CommandComputeTax) -> ResponseComputeTax:
            return ResponseComputeTax(tax=16)

    with pytest.raises(
        DTOAlreadyRegistered, match="Peru replaces ComputeTax.*Bolivia already"
    ):

        @billing.feature(CommandComputeTax, replaces=ComputeTax)
        class Peru(Feature):
            def execute(self, dto: CommandComputeTax) -> ResponseComputeTax:
                return ResponseComputeTax(tax=18)


def test_replacing_before_the_core_registered_is_refused():
    billing = UseFramework("billing-empty", log_after_execution=False)

    class ComputeTax(Feature):
        def execute(self, dto: Any) -> None:
            return None

    with pytest.raises(UnknownDTOToExecute, match="nothing answers CommandComputeTax yet"):

        @billing.feature(CommandComputeTax, replaces=ComputeTax)
        class Early(Feature):
            def execute(self, dto: Any) -> None:
                return None


def test_a_replacement_answering_an_incompatible_response_is_refused():
    billing, ComputeTax = _billing()

    with pytest.raises(TypeError, match="Broken.*UnrelatedResponse.*ResponseComputeTax"):

        @billing.feature(CommandComputeTax, replaces=ComputeTax)
        class Broken(Feature):
            def execute(self, dto: CommandComputeTax) -> UnrelatedResponse:
                return UnrelatedResponse()


def test_a_refused_replacement_over_several_commands_replaces_none_of_them():
    billing, ComputeTax = _billing()

    @billing.feature(CommandCheckout)
    class Checkout(Feature):
        def execute(self, dto: CommandCheckout) -> None:
            return None

    with pytest.raises(DTOAlreadyRegistered, match="CommandCheckout is handled by Checkout"):

        @billing.feature([CommandComputeTax, CommandCheckout], replaces=ComputeTax)
        class Both(Feature):
            def execute(self, dto: Any) -> ResponseComputeTax:
                return ResponseComputeTax(tax=0)

    assert _tax(billing).tax == 13


def test_without_replaces_a_second_handler_is_still_refused():
    billing, _ = _billing()

    with pytest.raises(DTOAlreadyRegistered):

        @billing.feature(CommandComputeTax)
        class Duplicate(Feature):
            def execute(self, dto: Any) -> None:
                return None


# ---------------------------------------------------------------------------------------------
# Visible
# ---------------------------------------------------------------------------------------------


def test_building_the_bus_says_who_replaced_whom():
    billing, ComputeTax = _billing()

    @billing.feature(CommandComputeTax, replaces=ComputeTax)
    class ComputeTaxBolivia(Feature):
        def execute(self, dto: CommandComputeTax) -> ResponseComputeTax:
            return ResponseComputeTax(tax=16)

    with capture_logs() as logs:
        billing.build_root_bus()

    assert any(
        "CommandComputeTax is handled by ComputeTaxBolivia (replaces ComputeTax)"
        in line["event"]
        for line in logs
    )


def test_introspection_describes_the_handler_that_runs_and_what_it_replaced():
    billing, ComputeTax = _billing()

    @billing.feature(CommandComputeTax, replaces=ComputeTax)
    class ComputeTaxBolivia(Feature):
        def execute(self, dto: CommandComputeTax) -> ResponseComputeTax:
            return ResponseComputeTax(tax=16)

    billing.build_root_bus()
    handling = describe(billing, CommandComputeTax)

    assert handling.type.__name__ == "ComputeTaxBolivia"
    assert [name.rsplit(".", 1)[-1] for name in handling.replaces] == ["ComputeTax"]
    assert features(billing)["CommandComputeTax"].replaces == handling.replaces


def test_an_application_service_is_replaced_the_same_way():
    billing = UseFramework("billing-app", log_after_execution=False)

    @billing.app_service(CommandCheckout)
    class Checkout(ApplicationService):
        def execute(self, dto: CommandCheckout) -> ResponseComputeTax:
            return ResponseComputeTax(tax=1)

    @billing.app_service(CommandCheckout, replaces=Checkout)
    class FastCheckout(ApplicationService):
        def execute(self, dto: CommandCheckout) -> ResponseComputeTax:
            return ResponseComputeTax(tax=2)

    answer = billing(CommandCheckout(amount=1), ResponseComputeTax)

    assert answer is not None and answer.tax == 2
    assert [
        n.rsplit(".", 1)[-1] for n in app_services(billing)["CommandCheckout"].replaces
    ] == ["Checkout"]
