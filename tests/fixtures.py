import dataclasses

import pytest

from sincpro_framework import ApplicationService, DataTransferObject, Feature, bus


class TestDTO(DataTransferObject):
    to_print: str


class TestFeature(Feature):
    def execute(self, dto: TestDTO):
        return dto


@pytest.fixture()
def feature_instance_test() -> Feature:
    return TestFeature()


@pytest.fixture()
def feature_bus_instance(feature_instance_test) -> bus.FeatureBus:
    feat_bus = bus.FeatureBus()
    feat_bus.register_feature(TestDTO, feature_instance_test)
    return feat_bus


class TestDTO2(DataTransferObject):
    to_print: str


class TestApplicationService(ApplicationService):
    def execute(self, dto: TestDTO):
        res = self.feature_bus.execute(TestDTO(to_print=dto.to_print))
        return res


@pytest.fixture()
def app_service_instance_test(feature_bus_instance) -> ApplicationService:
    app_services_instance = TestApplicationService(feature_bus_instance)
    return app_services_instance


@pytest.fixture()
def app_service_bus_instance(
    feature_bus_instance, app_service_instance_test
) -> bus.ApplicationServiceBus:
    # Adding application service to application service bus
    app_serv_bus = bus.ApplicationServiceBus()
    app_serv_bus.register_app_service(TestDTO2, app_service_instance_test)
    return app_serv_bus
