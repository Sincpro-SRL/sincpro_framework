import dataclasses

import pytest

from sincpro_framework import ApplicationService, DataTransferObject, Feature, bus
from sincpro_framework.exceptions import DTOAlreadyRegistered, UnknownDTOToExecute

from .fixtures import TestDTO, TestDTO2


def test_feature_bus(feature_bus_instance, feature_instance_test):
    assert feature_bus_instance.feature_registry[TestDTO.__name__] == feature_instance_test
    assert feature_bus_instance.execute(TestDTO(to_print="Hello World")).to_print == "Hello World"


def test_application_service_bus(app_service_bus_instance, app_service_instance_test):
    assert (
        app_service_bus_instance.app_service_registry[TestDTO2.__name__]
        == app_service_instance_test
    )
    assert app_service_bus_instance.execute(TestDTO2(to_print="Hello World")).to_print == "Hello World"


def test_framework_bus(feature_bus_instance, app_service_bus_instance):
    framework_bus = bus.FrameworkBus(feature_bus_instance, app_service_bus_instance)

    assert (
        framework_bus.execute(TestDTO(to_print="Executed in Feature bus")).to_print
        == "Executed in Feature bus"
    )
    assert (
        framework_bus.execute(TestDTO2(to_print="Executed in Application service bus")).to_print
        == "Executed in Application service bus"
    )


def test_framework_bus_raise_exception_unknown_dto_executed(
    feature_bus_instance, app_service_bus_instance
):
    class TestDTO3(DataTransferObject):
        something: str

    framework_bus = bus.FrameworkBus(feature_bus_instance, app_service_bus_instance)

    with pytest.raises(UnknownDTOToExecute):
        framework_bus.execute(TestDTO3(something="Executed in Application service bus"))


def test_build_framework_bus_with_same_dtos_in_feat_bus_and_app_ser_bus(
    feature_bus_instance,
    app_service_bus_instance,
    feature_instance_test,
    app_service_instance_test,
):
    class AnyDTO(DataTransferObject):
        any_reponse: str

    feature_bus_instance.register_feature(AnyDTO, feature_instance_test)
    app_service_bus_instance.register_app_service(AnyDTO, app_service_instance_test)

    with pytest.raises(DTOAlreadyRegistered):
        bus.FrameworkBus(feature_bus_instance, app_service_bus_instance)


def test_error_handlers_bus(
    feature_bus_instance,
    app_service_bus_instance,
):
    """
    Test if the execute method of the bus is able to handle errors based on injected error_handler fn
    This test cover for feature bus and app service bus and framework bus

    """
    expected_message = "Error handled"

    class AnyException(Exception):
        pass

    def error_handler(error):
        if isinstance(error, AnyException):
            return expected_message

        if isinstance(error, Exception):
            raise error

    # ---------------------------------------------------------------------------------------------
    # Feature level
    # ---------------------------------------------------------------------------------------------
    class AnyDTOFeature(DataTransferObject):
        pass

    class FeatureWithException(Feature):
        def execute(self, dto: AnyDTOFeature) -> AnyDTOFeature:
            raise AnyException("Error handled")

    feature_bus_instance.handle_error = error_handler
    feature_bus_instance.register_feature(AnyDTOFeature, FeatureWithException())

    assert feature_bus_instance.execute(AnyDTOFeature()) == expected_message

    # ---------------------------------------------------------------------------------------------
    # App service level
    # ---------------------------------------------------------------------------------------------
    class AnyDTOFAppServices(DataTransferObject):
        pass

    class AppServicesWithException(ApplicationService):
        def execute(self, dto: AnyDTOFeature) -> AnyDTOFeature:
            raise AnyException("Error handled")

    app_service_bus_instance.handle_error = error_handler
    app_service_bus_instance.register_app_service(
        AnyDTOFAppServices, AppServicesWithException(feature_bus_instance)
    )

    assert app_service_bus_instance.execute(AnyDTOFAppServices()) == expected_message

    # ---------------------------------------------------------------------------------------------
    # Framework level
    # ---------------------------------------------------------------------------------------------
    framework_bus = bus.FrameworkBus(feature_bus_instance, app_service_bus_instance)
    framework_bus.handle_error = error_handler
    assert feature_bus_instance.execute(AnyDTOFeature()) == expected_message
    assert app_service_bus_instance.execute(AnyDTOFAppServices()) == expected_message

    # ---------------------------------------------------------------------------------------------
    # test propagate error
    # ---------------------------------------------------------------------------------------------
    class ExceptionToPropagate(Exception):
        pass

    def error_handler_propagate(error):
        if isinstance(error, ExceptionToPropagate):
            raise AnyException("Error handled")
        if isinstance(error, AnyException):
            return expected_message
        if isinstance(error, Exception):
            return error

    class DTOToPropagate(DataTransferObject):
        pass

    class FeatureWithExceptionToPropagate(Feature):
        def execute(self, dto: DTOToPropagate) -> AnyDTOFeature:
            raise ExceptionToPropagate("Raise exception")

    feature_bus_instance.handle_error = error_handler_propagate
    feature_bus_instance.register_feature(DTOToPropagate, FeatureWithExceptionToPropagate())

    class DTOHandledError(DataTransferObject):
        pass

    class AppServicesHandledError(ApplicationService):
        def execute(self, dto: DTOHandledError) -> AnyDTOFeature:
            return self.feature_bus.execute(DTOToPropagate())

    app_service_bus_instance.handle_error = error_handler_propagate
    app_service_bus_instance.register_app_service(
        DTOHandledError, AppServicesHandledError(feature_bus_instance)
    )

    assert app_service_bus_instance.execute(DTOHandledError()) == expected_message
