"""feature_registry/app_service_registry dispatch by class, not by name — two classes that
share a `__name__` from different modules must never collide. dto_registry stays name-keyed
on purpose (Subscriber and BackgroundQueue only ever have a name, never the class, once an
event crosses a process boundary), so it needs its own collision guard instead.
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


def test_two_classes_sharing_a_name_dispatch_to_their_own_feature():
    CommandA = _make_command()
    CommandB = _make_command_from_a_different_place()
    bus = UseFramework("collision-dispatch", log_after_execution=False)

    @bus.feature(CommandA)
    class FeatureA(Feature):
        def execute(self, dto):
            return "from A"

    @bus.feature(CommandB)
    class FeatureB(Feature):
        def execute(self, dto):
            return "from B"

    assert bus(CommandA()) == "from A"
    assert bus(CommandB()) == "from B"


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


def test_a_plain_dto_is_keyed_by_module_and_qualname_in_dto_registry():
    Command = _make_command()
    bus = UseFramework("module-qualified", log_after_execution=False)

    @bus.feature(Command)
    class SomeFeature(Feature):
        def execute(self, dto):
            return None

    expected_key = f"{Command.__module__}.{Command.__qualname__}"
    assert bus.dto_registry[expected_key] is Command
    assert "Command" not in bus.dto_registry


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


def test_dto_registry_is_a_live_property_built_lazily():
    Command = _make_command()
    bus = UseFramework("lazy-property", log_after_execution=False)
    assert bus.was_initialized is False

    @bus.feature(Command)
    class SomeFeature(Feature):
        def execute(self, dto):
            return None

    assert bus.was_initialized is False
    expected_key = f"{Command.__module__}.{Command.__qualname__}"
    assert expected_key in bus.dto_registry
    assert bus.was_initialized is True


def test_map_to_dto_or_event_rebuilds_a_plain_dto_from_json_text():
    class CommandGreet(DataTransferObject):
        name: str

    bus = UseFramework("map-dto-json", log_after_execution=False)

    @bus.feature(CommandGreet)
    class GreetFeature(Feature):
        def execute(self, dto: CommandGreet) -> str:
            return f"hello {dto.name}"

    key = f"{CommandGreet.__module__}.{CommandGreet.__qualname__}"
    rebuilt = bus.map_to_dto_or_event(key, '{"name": "ana"}')

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

    key = f"{CommandGreet.__module__}.{CommandGreet.__qualname__}"
    rebuilt = bus.map_to_dto_or_event(key, {"name": "ana"})

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
