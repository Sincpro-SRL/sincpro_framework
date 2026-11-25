"""Asking whether someone may run a use case, without running it — the same decision the bus's
guard takes, so a catalog lists exactly what its caller may call."""

import pytest

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.auth import AccessCall, AccessControl, Identity, Permission, security
from sincpro_framework.auth.common.domain import exceptions
from sincpro_framework.context import EntrypointKind
from sincpro_framework.data_layer.store import StoreUnavailable
from sincpro_framework.entrypoints.infrastructure.http import merge_http_context


class SalesPermission(Permission):
    QUOTE = "sales.quote.create"


class CommandCreateQuote(DataTransferObject):
    total: int


class CommandPing(DataTransferObject):
    pass


def sales() -> tuple[UseFramework, AccessControl[SalesPermission]]:
    auth = AccessControl[SalesPermission]()
    bus = UseFramework("sales-may-run", log_after_execution=False)

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


def test_what_declares_nothing_may_be_run_by_anyone():
    bus, auth = sales()

    assert auth.may_run(bus, CommandPing, GUEST) is True
    assert auth.may_run(bus, CommandPing, Identity.anonymous()) is True


def test_the_requirements_of_a_command_on_one_bus():
    bus, auth = sales()

    declaration = auth.requirements_of(CommandCreateQuote, bus=bus)

    assert declaration is not None and SalesPermission.QUOTE in declaration.requirements
    assert auth.requirements_of(CommandPing, bus=bus) is None


def test_a_bus_nobody_guards_runs_anything_it_is_asked_about():
    _, auth = sales()
    other = UseFramework("sales-unguarded", log_after_execution=False)

    assert auth.may_run(other, CommandCreateQuote, GUEST)
    assert auth.refusal_of(other, CommandCreateQuote, GUEST) is None


def test_a_bus_another_access_control_guards_is_answered_by_that_one():
    bus, guarding = sales()
    asking = AccessControl[SalesPermission]()

    assert asking.may_run(bus, CommandCreateQuote, SELLER)
    assert not asking.may_run(bus, CommandCreateQuote, GUEST)
    assert str(asking.refusal_of(bus, CommandCreateQuote, GUEST)) == str(
        guarding.refusal_of(bus, CommandCreateQuote, GUEST)
    )


def test_a_rule_whose_store_is_down_is_never_read_as_a_refusal():
    """The incident: a catalog asked `may_run` while the rule's store was down, and the use case
    vanished from it as if refused — a 503 to retry became "you may not"."""
    auth = AccessControl[SalesPermission]()
    bus = UseFramework("sales-rule-store-down", log_after_execution=False)

    def store_down(call: AccessCall) -> bool:
        raise StoreUnavailable("the rule's store did not answer")

    @bus.feature(CommandCreateQuote)
    @auth.requires(auth.rule("quota left", store_down))
    class CreateQuote(Feature):
        def execute(self, dto: CommandCreateQuote) -> None:
            return None

    auth.on(bus)
    with pytest.raises(StoreUnavailable):
        auth.may_run(bus, CommandCreateQuote, SELLER)


def test_a_rule_that_reads_the_command_is_refused_without_one():
    auth = AccessControl[SalesPermission]()
    bus = UseFramework("sales-rule-reads-command", log_after_execution=False)

    @bus.feature(CommandCreateQuote)
    @auth.requires(auth.rule("small quote", lambda call: call.dto.total < 100))
    class CreateQuote(Feature):
        def execute(self, dto: CommandCreateQuote) -> None:
            return None

    auth.on(bus)
    refused = auth.refusal_of(bus, CommandCreateQuote, SELLER)
    assert isinstance(refused, exceptions.PermissionDenied)
    assert "cannot be judged without the Command" in refused.reason


def test_may_run_refuses_a_tenant_the_caller_named_that_is_not_its_own():
    """The incident: a catalog listed a tool for ana of `bo` whose call named tenant `pe` — the
    run refused it, the listing did not."""
    bus, auth = sales()
    ana_bo = Identity.user("ana", tenant="bo", permissions=[SalesPermission.QUOTE])
    carried = merge_http_context({"Baggage": "tenant_id=pe"})
    with bus.context(carried, kind=EntrypointKind.REST):
        assert not auth.may_run(bus, CommandCreateQuote, ana_bo)
        assert isinstance(
            auth.refusal_of(bus, CommandCreateQuote, ana_bo), exceptions.PermissionDenied
        )
    assert auth.may_run(bus, CommandCreateQuote, ana_bo)
