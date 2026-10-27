"""Asking whether someone may run a use case, without running it — the same decision the bus's
guard takes, so a catalog lists exactly what its caller may call."""

import pytest

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.auth import AccessControl, Identity, Permission, security
from sincpro_framework.auth.domain import exceptions
from sincpro_framework.exceptions import ProgrammingError


class SalesPermission(Permission):
    QUOTE = "sales.quote.create"


class CommandCreateQuote(DataTransferObject):
    total: int


class CommandPing(DataTransferObject):
    pass


def sales(strict: bool = False) -> tuple[UseFramework, AccessControl[SalesPermission]]:
    auth = AccessControl[SalesPermission](strict=strict)
    bus = UseFramework(f"sales-may-run-{strict}", log_after_execution=False)

    @bus.feature(CommandCreateQuote)
    @auth.requires(SalesPermission.QUOTE)
    class CreateQuote(Feature):
        def execute(self, dto: CommandCreateQuote) -> None:
            return None

    @bus.feature(CommandPing)
    class Ping(Feature):
        def execute(self, dto: CommandPing) -> None:
            return None

    auth.on(bus)
    return bus, auth


SELLER = Identity.user("ana", permissions=[SalesPermission.QUOTE])
GUEST = Identity.user("eve")


def test_may_run_answers_as_the_guard_decides():
    bus, auth = sales()

    assert auth.may_run(bus, CommandCreateQuote, SELLER) is True
    assert auth.may_run(bus, CommandCreateQuote, GUEST) is False
    assert isinstance(
        auth.refusal_of(bus, CommandCreateQuote, GUEST), exceptions.PermissionDenied
    )


def test_the_identity_in_play_is_asked_when_none_is_given():
    bus, auth = sales()

    with security.as_identity(SELLER):
        assert auth.may_run(bus, CommandCreateQuote)
    with security.as_identity(GUEST):
        assert not auth.may_run(bus, CommandCreateQuote)


def test_what_declares_nothing_runs_unless_strict():
    bus, auth = sales()
    strict_bus, strict_auth = sales(strict=True)

    assert auth.may_run(bus, CommandPing, GUEST) is True
    assert strict_auth.may_run(strict_bus, CommandPing, GUEST) is False


def test_the_requirements_of_a_command_on_one_bus():
    bus, auth = sales()

    declaration = auth.requirements_of(CommandCreateQuote, bus=bus)

    assert declaration is not None and SalesPermission.QUOTE in declaration.requirements
    assert auth.requirements_of(CommandPing, bus=bus) is None


def test_asking_about_a_bus_it_does_not_guard_says_so():
    _, auth = sales()
    other = UseFramework("sales-unguarded", log_after_execution=False)

    with pytest.raises(ProgrammingError, match="does not guard 'sales-unguarded'"):
        auth.may_run(other, CommandCreateQuote, SELLER)
