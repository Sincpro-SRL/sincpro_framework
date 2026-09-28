"""`unregistered_dependencies`: every name a Feature or ApplicationService declares for the IDE
that nobody registered — the check Spring and NestJS make at startup, asked for by a test."""

from sincpro_framework import ApplicationService, DataTransferObject, Feature, UseFramework
from sincpro_framework.testing import unregistered_dependencies


class Billing:
    pass


class Ledger:
    pass


class DependencyContextType:
    billing: Billing
    ledger: Ledger


class CommandCharge(DataTransferObject):
    pass


class CommandClose(DataTransferObject):
    pass


def _bus(register_ledger: bool) -> UseFramework:
    bus = UseFramework("deps-check", log_after_execution=False)
    bus.add_dependency("billing", Billing())
    if register_ledger:
        bus.add_dependency("ledger", Ledger())

    @bus.feature(CommandCharge)
    class Charge(Feature, DependencyContextType):
        def execute(self, dto: CommandCharge) -> None: ...

    @bus.app_service(CommandClose)
    class Close(ApplicationService):
        ledger: Ledger

        def execute(self, dto: CommandClose) -> None: ...

    return bus


def test_a_declared_dependency_nobody_registered_is_named_with_its_handler():
    missing = unregistered_dependencies(_bus(register_ledger=False))

    assert missing == {
        f"{__name__}._bus.<locals>.Charge": ("ledger",),
        f"{__name__}._bus.<locals>.Close": ("ledger",),
    }


def test_nothing_is_missing_once_everything_is_registered():
    assert unregistered_dependencies(_bus(register_ledger=True)) == {}


def test_what_the_framework_itself_gives_a_handler_is_never_reported():
    bus = UseFramework("deps-check-framework", log_after_execution=False)

    @bus.app_service(CommandClose)
    class Close(ApplicationService):
        def execute(self, dto: CommandClose) -> None: ...

    assert unregistered_dependencies(bus) == {}
