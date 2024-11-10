"""Test Instance of FeatureBus"""

from sincpro_framework import Feature, bus

from ..fixtures import CommandFeatureTest1


def test_feature_bus(feature_bus_instance: bus.FeatureBus, feature_instance_test: Feature):
    assert (
        feature_bus_instance.feature_registry[CommandFeatureTest1.__name__]
        == feature_instance_test
    )
    assert (
        feature_bus_instance.execute(CommandFeatureTest1(to_print="Hello World")).to_print
        == "Hello World"
    )
