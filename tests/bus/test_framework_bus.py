"""Test instance of FrameworkBus Main facade bus"""

import pytest

from sincpro_framework import ApplicationService, DataTransferObject, Feature, bus
from sincpro_framework.exceptions import DTOAlreadyRegistered, UnknownDTOToExecute

from ..fixtures import (
    CommandApplicationService1,
    CommandFeatureTest1,
    ResponseApplicationService1,
    ResponseFeatureTest1,
)


def test_framework_bus(
    feature_bus_instance: bus.FeatureBus, app_service_bus_instance: bus.ApplicationServiceBus
):
    framework_bus = bus.FrameworkBus(feature_bus_instance, app_service_bus_instance)

    assert (
        framework_bus.execute(
            CommandFeatureTest1(to_print="Executed in Feature bus"), ResponseFeatureTest1
        ).to_print
        == "Executed in Feature bus"
    )
    assert (
        framework_bus.execute(
            CommandApplicationService1(to_print="Executed in Application service bus"),
            ResponseApplicationService1,
        ).to_print
        == "Executed in Application service bus"
    )


def test_framework_bus_raise_exception_unknown_dto_executed(
    feature_bus_instance: bus.FeatureBus, app_service_bus_instance: bus.ApplicationServiceBus
):
    class UnRegisteredDTO(DataTransferObject):
        something: str

    framework_bus = bus.FrameworkBus(feature_bus_instance, app_service_bus_instance)

    with pytest.raises(UnknownDTOToExecute):
        framework_bus.execute(
            UnRegisteredDTO(something="Executed in Application service bus")
        )


def test_build_framework_bus_with_same_dtos_in_feat_bus_and_app_ser_bus(
    feature_bus_instance: bus.FeatureBus,
    app_service_bus_instance: bus.ApplicationServiceBus,
    feature_instance_test: Feature,
    app_service_instance_test: ApplicationService,
):
    class AnyDTO(DataTransferObject):
        any_reponse: str

    feature_bus_instance.register_feature(AnyDTO, feature_instance_test)
    app_service_bus_instance.register_app_service(AnyDTO, app_service_instance_test)

    with pytest.raises(DTOAlreadyRegistered):
        bus.FrameworkBus(feature_bus_instance, app_service_bus_instance)
