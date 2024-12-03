"""Test multiple instances of a container."""

from sincpro_framework import DataTransferObject, Feature, UseFramework


class Command1(DataTransferObject):
    pass


class Command2(DataTransferObject):
    pass


def test_multiple_instances():
    """Multiples"""
    f1 = UseFramework("app-1")
    f2 = UseFramework("app-2")

    @f1.feature(Command1)
    class FeatApp1(Feature):
        def execute(self, dto):
            pass

    @f2.feature(Command2)
    class FeatApp2(Feature):
        def execute(self, dto):
            pass

    f1.build_root_bus()
    f2.build_root_bus()

    assert (
        f1.bus.feature_bus.feature_registry.keys()
        != f2.bus.feature_bus.feature_registry.keys()
    )
