"""Test instance of ApplicationServiceBus"""

from sincpro_framework import ApplicationService, bus

from ..fixtures import CommandApplicationService1, ResponseApplicationService1


def test_application_service_bus(
    app_service_bus_instance: bus.ApplicationServiceBus,
    app_service_instance_test: ApplicationService,
):
    assert (
        app_service_bus_instance.app_service_registry[CommandApplicationService1.__name__]
        == app_service_instance_test
    )
    assert (
        app_service_bus_instance.execute(
            CommandApplicationService1(to_print="Hello World"), ResponseApplicationService1
        ).to_print
        == "Hello World"
    )
