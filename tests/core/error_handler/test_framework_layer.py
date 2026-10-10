"""Error handling in the framework layer"""

from typing import cast

import pytest

from sincpro_framework import ApplicationService, DataTransferObject, Feature, bus


class FeatureExceptionLayer(Exception):
    pass


class ApplicationExceptionLayer(Exception):
    pass


class FrameworkExceptionLayer(Exception):
    pass


def test_error_handler_framework_layer__handled_error(
    feature_bus_instance: bus.FeatureBus,
    app_service_bus_instance: bus.ApplicationServiceBus,
):
    """
    Test if the execute method of the bus is able to handle errors based on injected error_handler fn
    This test cover for feature bus and app service bus and framework bus

    """
    expected_message = "Error handled"

    def error_handler(error):
        if isinstance(error, FeatureExceptionLayer):
            return expected_message

        if isinstance(error, Exception):
            raise error

    # ---------------------------------------------------------------------------------------------
    # Feature level
    # ---------------------------------------------------------------------------------------------
    class CommandAnyDTOFeature(DataTransferObject):
        pass

    class FeatureWithException(Feature):
        def execute(self, dto: CommandAnyDTOFeature) -> CommandAnyDTOFeature:
            raise FeatureExceptionLayer("Error handled")

    feature_bus_instance.handle_error = error_handler
    feature_bus_instance.register_feature(CommandAnyDTOFeature, FeatureWithException())

    assert feature_bus_instance.execute(CommandAnyDTOFeature()) == expected_message

    # ---------------------------------------------------------------------------------------------
    # App service level
    # ---------------------------------------------------------------------------------------------
    class CommandAnyDTOAppServices(DataTransferObject):
        pass

    class AppServicesWithException(ApplicationService):
        def execute(self, dto: CommandAnyDTOFeature) -> CommandAnyDTOFeature:
            raise FeatureExceptionLayer("Error handled")

    app_service_bus_instance.handle_error = error_handler
    app_service_bus_instance.register_app_service(
        CommandAnyDTOAppServices, AppServicesWithException(feature_bus_instance)
    )

    assert app_service_bus_instance.execute(CommandAnyDTOAppServices()) == expected_message

    # ---------------------------------------------------------------------------------------------
    # Framework level
    # ---------------------------------------------------------------------------------------------
    framework_bus = bus.FrameworkBus(feature_bus_instance, app_service_bus_instance)

    assert feature_bus_instance.execute(CommandAnyDTOFeature()) == expected_message
    assert app_service_bus_instance.execute(CommandAnyDTOAppServices()) == expected_message
    assert framework_bus.execute(CommandAnyDTOFeature()) == expected_message


def test_error_handler_framework_layer__propagated_error_from_feature_layer(
    feature_bus_instance: bus.FeatureBus,
    app_service_bus_instance: bus.ApplicationServiceBus,
):
    """
    Test if the execute method of the bus is able to handle errors based on injected error_handler fn
    This test cover for feature bus and app service bus and framework bus

    """

    # ---------------------------------------------------------------------------------------------
    # Feature level
    # ---------------------------------------------------------------------------------------------
    class CommandAnyDTOFeature(DataTransferObject):
        pass

    class FeatureWithException(Feature):
        def execute(self, dto: CommandAnyDTOFeature) -> CommandAnyDTOFeature:
            raise FeatureExceptionLayer("Error handled")

    def feat_error_handler(error):
        if isinstance(error, FeatureExceptionLayer):
            raise FrameworkExceptionLayer("Error at applciation layer")

    feature_bus_instance.handle_error = feat_error_handler
    feature_bus_instance.register_feature(CommandAnyDTOFeature, FeatureWithException())

    # ---------------------------------------------------------------------------------------------
    # App service level
    # ---------------------------------------------------------------------------------------------
    class CommandAnyDTOAppServices(DataTransferObject):
        pass

    class AppServicesWithException(ApplicationService):
        def execute(self, dto: CommandAnyDTOAppServices) -> CommandAnyDTOAppServices:
            """Execute the feature layer"""
            return cast(
                self.feature_bus.execute(CommandAnyDTOFeature()), CommandAnyDTOAppServices
            )

    app_service_bus_instance.register_app_service(
        CommandAnyDTOAppServices, AppServicesWithException(feature_bus_instance)
    )

    with pytest.raises(FrameworkExceptionLayer):
        app_service_bus_instance.execute(CommandAnyDTOAppServices())

    # ---------------------------------------------------------------------------------------------
    # Framework level
    # ---------------------------------------------------------------------------------------------
    framework_bus = bus.FrameworkBus(feature_bus_instance, app_service_bus_instance)

    with pytest.raises(FrameworkExceptionLayer):
        framework_bus.execute(CommandAnyDTOFeature())
