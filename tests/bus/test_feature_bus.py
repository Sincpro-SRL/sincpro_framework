"""Test Instance of FeatureBus"""

from sincpro_framework import (
    ApplicationService,
    DataTransferObject,
    Feature,
    UseFramework,
    bus,
)

from ..fixtures import CommandFeatureTest1, ResponseFeatureTest1


def test_feature_bus(feature_bus_instance: bus.FeatureBus, feature_instance_test: Feature):
    assert feature_bus_instance.feature_registry[CommandFeatureTest1] == feature_instance_test
    assert (
        feature_bus_instance.execute(
            CommandFeatureTest1(to_print="Hello World"), ResponseFeatureTest1
        ).to_print
        == "Hello World"
    )


def test_inside_an_application_service_the_feature_bus_is_called_like_the_root_bus():
    class CommandTotal(DataTransferObject):
        amount: int

    framework = UseFramework("callable-feature-bus", log_after_execution=False)

    @framework.feature(CommandFeatureTest1)
    class Echo(Feature):
        def execute(self, dto: CommandFeatureTest1) -> ResponseFeatureTest1:
            return ResponseFeatureTest1(to_print=dto.to_print)

    @framework.app_service(CommandTotal)
    class Total(ApplicationService):
        def execute(self, dto: CommandTotal) -> ResponseFeatureTest1:
            typed = self.feature_bus(
                CommandFeatureTest1(to_print="typed"), ResponseFeatureTest1
            )
            untyped = self.feature_bus(CommandFeatureTest1(to_print="untyped"))
            assert isinstance(untyped, ResponseFeatureTest1)
            return ResponseFeatureTest1(to_print=f"{typed.to_print} {untyped.to_print}")

    assert framework(CommandTotal(amount=1), ResponseFeatureTest1).to_print == "typed untyped"
