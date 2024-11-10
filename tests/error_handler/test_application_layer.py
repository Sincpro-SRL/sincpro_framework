"""Error handler test at application layer"""

import pytest

from sincpro_framework import ApplicationService, DataTransferObject, Feature, bus


# ---------------------------------------------------------------------------------------------
# Exception to propagate
# ---------------------------------------------------------------------------------------------
class ExceptionToFeatureLayer(Exception):
    pass


class ExceptionAtApplicationLayer(Exception):
    pass


def test_error_handler_application_layer_handled_error(
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
    class CommandDTOFeatureLayer(DataTransferObject):
        pass

    class FeatureWithExceptionToPropagate(Feature):
        def execute(self, dto: CommandDTOFeatureLayer) -> CommandDTOFeatureLayer:
            raise ExceptionToFeatureLayer("Raise exception")

    def error_handler_at_feature_layer(error):
        if isinstance(error, ExceptionToFeatureLayer):
            raise ExceptionAtApplicationLayer("Error handled")
        if isinstance(error, Exception):
            return error

    feature_bus_instance.handle_error = error_handler_at_feature_layer
    feature_bus_instance.register_feature(
        CommandDTOFeatureLayer, FeatureWithExceptionToPropagate()
    )

    # ---------------------------------------------------------------------------------------------
    # App service level
    # ---------------------------------------------------------------------------------------------
    class CommandDTOApplicationLayer(DataTransferObject):
        pass

    class AppServicesHandledError(ApplicationService):
        def execute(self, dto: CommandDTOApplicationLayer) -> CommandDTOApplicationLayer:
            return self.feature_bus.execute(CommandDTOFeatureLayer())

    expected_message = "Error handled"

    def error_handler_at_app_layer(error):
        if isinstance(error, ExceptionAtApplicationLayer):
            return expected_message
        if isinstance(error, Exception):
            return error

    app_service_bus_instance.handle_error = error_handler_at_app_layer
    app_service_bus_instance.register_app_service(
        CommandDTOApplicationLayer, AppServicesHandledError(feature_bus_instance)
    )

    assert app_service_bus_instance.execute(CommandDTOApplicationLayer()) == expected_message


def test_error_handler_application_layer_NO_handledError(
    feature_bus_instance: bus.FeatureBus,
    app_service_bus_instance: bus.ApplicationServiceBus,
):
    """
    Test raise exception despite the error_handler at application layer
    """

    # ---------------------------------------------------------------------------------------------
    # Feature level
    # ---------------------------------------------------------------------------------------------
    class CommandDTOFeatureLayer(DataTransferObject):
        pass

    class FeatureWithExceptionToPropagate(Feature):
        def execute(self, dto: CommandDTOFeatureLayer) -> CommandDTOFeatureLayer:
            raise ExceptionToFeatureLayer("Raise exception")

    def error_handler_at_feature_layer(error):
        if isinstance(error, ExceptionToFeatureLayer):
            raise ExceptionAtApplicationLayer("Error handled")
        if isinstance(error, Exception):
            return error

    feature_bus_instance.handle_error = error_handler_at_feature_layer
    feature_bus_instance.register_feature(
        CommandDTOFeatureLayer, FeatureWithExceptionToPropagate()
    )

    # ---------------------------------------------------------------------------------------------
    # App service level
    # ---------------------------------------------------------------------------------------------
    class CommandDTOApplicationLayer(DataTransferObject):
        pass

    class AppServicesHandledError(ApplicationService):
        def execute(self, dto: CommandDTOApplicationLayer) -> CommandDTOApplicationLayer:
            return self.feature_bus.execute(CommandDTOFeatureLayer())

    expected_message = "Error handled"

    def error_handler_at_app_layer(error):
        if isinstance(error, ExceptionAtApplicationLayer):
            raise error
        if isinstance(error, Exception):
            return error

    app_service_bus_instance.handle_error = error_handler_at_app_layer
    app_service_bus_instance.register_app_service(
        CommandDTOApplicationLayer, AppServicesHandledError(feature_bus_instance)
    )

    with pytest.raises(ExceptionAtApplicationLayer):
        assert (
            app_service_bus_instance.execute(CommandDTOApplicationLayer()) == expected_message
        )
