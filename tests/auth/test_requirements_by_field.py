"""What a use case requires may depend on what it was asked — `auth.by_field(...)` — and a
provider may word the refusal of a whole `any_of`."""

from enum import StrEnum

import pytest

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.auth import (
    AccessControl,
    AuthProvider,
    Credentials,
    Identity,
    Permission,
    security,
)
from sincpro_framework.auth.domain import exceptions


class FinancePermission(Permission):
    RECEIVE = "finance.payment.receive"
    PAY = "finance.payment.pay"


class Direction(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"
    INTERNAL = "internal"


class CommandRegisterPayment(DataTransferObject):
    direction: Direction
    amount: int


def finance() -> tuple[UseFramework, AccessControl[FinancePermission]]:
    auth = AccessControl[FinancePermission]()
    bus = UseFramework("finance-by-field", log_after_execution=False)

    @bus.feature(CommandRegisterPayment)
    @auth.requires(
        auth.by_field(
            "direction",
            {
                Direction.INBOUND: FinancePermission.RECEIVE,
                Direction.OUTBOUND: FinancePermission.PAY,
            },
        )
    )
    class RegisterPayment(Feature):
        def execute(self, dto: CommandRegisterPayment) -> str:
            return f"{dto.direction} {dto.amount}"

    auth.on(bus)
    return bus, auth


CASHIER = Identity.user("ana", permissions=[FinancePermission.RECEIVE])


def test_the_field_of_the_command_chooses_what_is_required():
    bus, _ = finance()

    with security.as_identity(CASHIER):
        assert (
            bus(CommandRegisterPayment(direction=Direction.INBOUND, amount=5)) == "inbound 5"
        )
        with pytest.raises(exceptions.PermissionDenied, match="finance.payment.pay"):
            bus(CommandRegisterPayment(direction=Direction.OUTBOUND, amount=5))


def test_a_value_it_names_no_permission_for_is_refused():
    bus, _ = finance()

    with security.as_identity(CASHIER), pytest.raises(
        exceptions.PermissionDenied, match="names no permission"
    ):
        bus(CommandRegisterPayment(direction=Direction.INTERNAL, amount=5))


def test_asked_with_no_command_it_is_one_among_them():
    bus, auth = finance()

    assert auth.may_run(bus, CommandRegisterPayment, CASHIER) is True
    assert auth.may_run(bus, CommandRegisterPayment, Identity.user("eve")) is False
    outbound = CommandRegisterPayment(direction=Direction.OUTBOUND, amount=1)
    assert auth.may_run(bus, CommandRegisterPayment, CASHIER, dto=outbound) is False


# --- a provider words the refusal of a whole any_of -------------------------------------------------


class AgentsProvider(AuthProvider):
    name = "agents"

    def authenticate(self, credentials: Credentials) -> Identity | None:
        return None

    def refusing_any(self, identity, permissions, reasons) -> str | None:
        agents = ", ".join(one.split(".")[-1] for one in permissions)
        return f"Este tenant no tiene habilitado ninguno de estos agentes: {agents}"


def test_a_provider_words_the_refusal_of_a_whole_any_of():
    auth = AccessControl[FinancePermission](providers=[AgentsProvider()])
    bus = UseFramework("finance-any-of", log_after_execution=False)

    @bus.feature(CommandRegisterPayment)
    @auth.requires(auth.any_of(FinancePermission.RECEIVE, FinancePermission.PAY))
    class RegisterPayment(Feature):
        def execute(self, dto: CommandRegisterPayment) -> None:
            return None

    auth.on(bus)
    nobody = Identity.user("eve").model_copy(update={"provider": "agents"})

    with security.as_identity(nobody), pytest.raises(exceptions.PermissionDenied) as refused:
        bus(CommandRegisterPayment(direction=Direction.INBOUND, amount=1))

    assert refused.value.reason == (
        "Este tenant no tiene habilitado ninguno de estos agentes: receive, pay"
    )


def test_without_its_own_words_the_reasons_are_joined():
    plain = AccessControl[FinancePermission]()
    plain_bus = UseFramework("finance-any-of-plain", log_after_execution=False)

    @plain_bus.feature(CommandRegisterPayment)
    @plain.requires(plain.any_of(FinancePermission.RECEIVE, FinancePermission.PAY))
    class RegisterPayment(Feature):
        def execute(self, dto: CommandRegisterPayment) -> None:
            return None

    plain.on(plain_bus)

    with security.as_identity(Identity.user("eve")), pytest.raises(
        exceptions.PermissionDenied
    ) as refused:
        plain_bus(CommandRegisterPayment(direction=Direction.INBOUND, amount=1))

    assert " and " in refused.value.reason
