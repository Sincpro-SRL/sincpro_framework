"""feature_registry/app_service_registry dispatch by class; every wire and workflow publishes a
use case by its name, so two classes sharing a `__name__` in one bus are refused when the second
is registered, naming both. dto_registry is keyed by identity — an event by its `name`, any other
DTO by `context.Class` — because a message that crossed a process boundary only has that.
"""

from dataclasses import dataclass

import pytest

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.exceptions import DTOAlreadyRegistered, UnknownDTOToExecute


def _make_command() -> type[DataTransferObject]:
    class Command(DataTransferObject):
        pass

    return Command


def _make_command_from_a_different_place() -> type[DataTransferObject]:
    class Command(DataTransferObject):
        pass

    return Command


def test_two_classes_sharing_a_name_are_refused_naming_both():
    """Dispatch is by class, but every wire and workflow publishes a use case by its name:
    the second class would vanish from all of them, so it is refused (PRD_29, G3)."""
    CommandA = _make_command()
    CommandB = _make_command_from_a_different_place()
    bus = UseFramework("collision-dispatch", log_after_execution=False)

    @bus.feature(CommandA)
    class FeatureA(Feature):
        def execute(self, dto):
            return "from A"

    with pytest.raises(DTOAlreadyRegistered) as refused:

        @bus.feature(CommandB)
        class FeatureB(Feature):
            def execute(self, dto):
                return "from B"

    message = str(refused.value)
    assert f"{CommandA.__module__}.{CommandA.__qualname__}" in message
    assert f"{CommandB.__module__}.{CommandB.__qualname__}" in message
    assert "rename one of the classes" in message


def test_a_feature_and_an_application_service_sharing_a_name_are_refused_too():
    """Workflows and the catalog know a Feature and an ApplicationService by one name space."""
    from sincpro_framework import ApplicationService

    CommandA = _make_command()
    CommandB = _make_command_from_a_different_place()
    bus = UseFramework("collision-layers", log_after_execution=False)

    @bus.feature(CommandA)
    class FeatureA(Feature):
        def execute(self, dto):
            return "feature"

    with pytest.raises(
        DTOAlreadyRegistered, match="Two use cases of this bus are named Command"
    ):

        @bus.app_service(CommandB)
        class ServiceB(ApplicationService):
            def execute(self, dto):
                return "service"


def test_registering_the_same_class_twice_raises():
    bus = UseFramework("real-duplicate", log_after_execution=False)

    @bus.feature(_Command := _make_command())
    class FirstFeature(Feature):
        def execute(self, dto):
            return "first"

    with pytest.raises(DTOAlreadyRegistered):

        @bus.feature(_Command)
        class SecondFeature(Feature):
            def execute(self, dto):
                return "second"


def test_a_plain_dto_is_keyed_by_its_context_and_class_name():
    Command = _make_command()
    bus = UseFramework("by-identity", log_after_execution=False)

    @bus.feature(Command)
    class SomeFeature(Feature):
        def execute(self, dto):
            return None

    assert bus.dto_registry["by-identity.Command"] is Command
    assert "Command" not in bus.dto_registry


def test_moving_a_class_to_another_module_keeps_its_identity():
    """The identity is the context and the class name: a refactor that moves a module never
    changes what a stored workflow, a queue message or another service calls it."""
    Here, There = _make_command(), _make_command_from_a_different_place()
    There.__module__ = "somewhere.else.entirely"
    here = UseFramework("moved", log_after_execution=False)
    there = UseFramework("moved", log_after_execution=False)
    here.feature(Here)(type("HereFeature", (Feature,), {"execute": lambda self, dto: "here"}))
    there.feature(There)(
        type("ThereFeature", (Feature,), {"execute": lambda self, dto: "there"})
    )

    assert set(here.dto_registry) == set(there.dto_registry) == {"moved.Command"}
    assert there(there.map_to_dto_or_event("moved.Command", {})) == "there"


def test_two_contexts_each_with_a_command_create_keep_two_identities():
    def context(name: str) -> UseFramework:
        class CommandCreate(DataTransferObject):
            title: str

        bus = UseFramework(name, log_after_execution=False)

        @bus.feature(CommandCreate)
        class Create(Feature):
            def execute(self, dto: CommandCreate) -> str:
                return f"{name} created {dto.title}"

        return bus

    sales, billing = context("sales"), context("billing")

    assert list(sales.dto_registry) == ["sales.CommandCreate"]
    assert list(billing.dto_registry) == ["billing.CommandCreate"]
    assert sales(sales.map_to_dto_or_event("sales.CommandCreate", {"title": "q"})) == (
        "sales created q"
    )
    assert billing(billing.map_to_dto_or_event("CommandCreate", {"title": "i"})) == (
        "billing created i"
    )
    with pytest.raises(UnknownDTOToExecute):
        sales.map_to_dto_or_event("billing.CommandCreate", {"title": "x"})


def test_a_domain_event_is_keyed_by_its_default_wire_name():
    @dataclass(kw_only=True)
    class SomethingHappened(DomainEvent):
        pass

    bus = UseFramework("default-wire-name", log_after_execution=False)

    @bus.feature(SomethingHappened)
    class SomeFeature(Feature):
        def execute(self, dto):
            return None

    assert bus.dto_registry["SomethingHappened"] is SomethingHappened


def test_a_domain_event_with_an_explicit_wire_name_is_keyed_by_it():
    @dataclass(kw_only=True)
    class RunAdvanced(DomainEvent):
        name = "sincpro.execution.v1.run_advanced"

    bus = UseFramework("explicit-wire-name", log_after_execution=False)

    @bus.feature(RunAdvanced)
    class SomeFeature(Feature):
        def execute(self, dto):
            return None

    assert bus.dto_registry["sincpro.execution.v1.run_advanced"] is RunAdvanced
    assert "RunAdvanced" not in bus.dto_registry


def test_two_domain_events_sharing_an_explicit_wire_name_raise():
    @dataclass(kw_only=True)
    class FirstEvent(DomainEvent):
        name = "sincpro.v1.collides"

    @dataclass(kw_only=True)
    class SecondEvent(DomainEvent):
        name = "sincpro.v1.collides"

    bus = UseFramework("wire-name-collision", log_after_execution=False)

    @bus.feature(FirstEvent)
    class FirstFeature(Feature):
        def execute(self, dto):
            return None

    with pytest.raises(DTOAlreadyRegistered):

        @bus.feature(SecondEvent)
        class SecondFeature(Feature):
            def execute(self, dto):
                return None


def test_dto_registry_is_a_live_property_that_builds_nothing():
    Command = _make_command()
    bus = UseFramework("lazy-property", log_after_execution=False)
    assert bus.was_initialized is False

    @bus.feature(Command)
    class SomeFeature(Feature):
        def execute(self, dto):
            return None

    assert bus.was_initialized is False
    assert "lazy-property.Command" in bus.dto_registry
    assert bus.was_initialized is False


def test_map_to_dto_or_event_rebuilds_a_plain_dto_from_json_text():
    class CommandGreet(DataTransferObject):
        name: str

    bus = UseFramework("map-dto-json", log_after_execution=False)

    @bus.feature(CommandGreet)
    class GreetFeature(Feature):
        def execute(self, dto: CommandGreet) -> str:
            return f"hello {dto.name}"

    rebuilt = bus.map_to_dto_or_event("map-dto-json.CommandGreet", '{"name": "ana"}')

    assert rebuilt == CommandGreet(name="ana")
    assert bus(rebuilt) == "hello ana"


def test_map_to_dto_or_event_rebuilds_a_plain_dto_from_a_dict():
    class CommandGreet(DataTransferObject):
        name: str

    bus = UseFramework("map-dto-dict", log_after_execution=False)

    @bus.feature(CommandGreet)
    class GreetFeature(Feature):
        def execute(self, dto: CommandGreet) -> str:
            return f"hello {dto.name}"

    rebuilt = bus.map_to_dto_or_event("map-dto-dict.CommandGreet", {"name": "ana"})

    assert rebuilt == CommandGreet(name="ana")


def test_map_to_dto_or_event_rebuilds_a_domain_event_by_its_wire_name():
    @dataclass(kw_only=True)
    class TicketClosed(DomainEvent):
        reason: str

    bus = UseFramework("map-event", log_after_execution=False)

    @bus.feature(TicketClosed)
    class CloseFeature(Feature):
        def execute(self, dto: TicketClosed) -> str:
            return f"closed: {dto.reason}"

    rebuilt = bus.map_to_dto_or_event("TicketClosed", '{"reason": "fixed"}')

    assert isinstance(rebuilt, TicketClosed)
    assert rebuilt.reason == "fixed"
    assert bus(rebuilt) == "closed: fixed"


def test_map_to_dto_or_event_raises_for_a_name_nobody_registered():
    bus = UseFramework("map-unknown", log_after_execution=False)

    with pytest.raises(UnknownDTOToExecute):
        bus.map_to_dto_or_event("NobodyKnowsThis", "{}")
