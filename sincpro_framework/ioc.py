"""Inversion of Control (IoC) container for the SincPro Framework"""

from typing import Any, Union

from dependency_injector import containers, providers
from dependency_injector.providers import Dict, Factory, Object, Singleton
from sincpro_log.logger import LoggerProxy

from .bus import ApplicationServiceBus, FeatureBus, FrameworkBus
from .exceptions import DTOAlreadyRegistered
from .sincpro_abstractions import TypeDTO

# ---------------------------------------------------------------------------------------------
# Container Definition
# ---------------------------------------------------------------------------------------------


class FrameworkContainer(containers.DeclarativeContainer):
    """Main container for the framework

    This container will initialize the
    main components of the framework at runtime.
    """

    logger_bus: Object[LoggerProxy] = providers.Object()

    injected_dependencies: Dict = providers.Dict()

    # DTO registry
    dto_registry: Dict = providers.Dict({})

    # atomic layer
    feature_registry: Dict = providers.Dict({})
    feature_bus: Singleton[FeatureBus] = providers.Singleton(FeatureBus, logger_bus)

    # orchestration layer
    app_service_registry: Dict = providers.Dict({})
    app_service_bus: Singleton[ApplicationServiceBus] = providers.Singleton(
        ApplicationServiceBus, logger_bus
    )

    # Facade
    framework_bus: Factory[FrameworkBus] = providers.Factory(
        FrameworkBus,
        feature_bus=feature_bus,
        app_service_bus=app_service_bus,
        dto_registry=dto_registry,
        logger_bus=logger_bus,
    )


# ---------------------------------------------------------------------------------------------
# Build processes
# ---------------------------------------------------------------------------------------------
def inject_feature_to_bus(
    framework_container: FrameworkContainer, dto: Union[str, list, TypeDTO, Any]
):
    def inner_fn(decorated_class):
        dtos = dto
        if not isinstance(dto, list):
            dtos = [dto]

        for data_transfer_object in dtos:
            if data_transfer_object.__name__ in framework_container.feature_registry.kwargs:
                raise DTOAlreadyRegistered(
                    f"The DTO: [{data_transfer_object.__name__} from {data_transfer_object.__module__}] is already registered"
                )

            framework_container.logger_bus.debug(
                f"Registering feature: [{data_transfer_object.__name__}]",
            )

            # --------------------------------------------------------------------
            # NEW: Register DTO class reference for introspection
            framework_container.dto_registry = providers.Dict(
                {
                    **{data_transfer_object.__name__: data_transfer_object},
                    **framework_container.dto_registry.kwargs,
                }
            )

            framework_container.logger_bus.debug(framework_container.dto_registry)

            # --------------------------------------------------------------------
            # Register DTO to fn builder that will return the feature instance
            framework_container.feature_registry = providers.Dict(
                {
                    **{
                        data_transfer_object.__name__: providers.Factory(
                            decorated_class,
                        )
                    },
                    **framework_container.feature_registry.kwargs,
                }
            )

            framework_container.feature_bus.add_attributes(
                feature_registry=framework_container.feature_registry
            )

        return decorated_class

    return inner_fn


def inject_app_service_to_bus(framework_container: FrameworkContainer, dto: Union[str, list]):
    def inner_fn(decorated_class):
        dtos = dto
        if not isinstance(dto, list):
            dtos = [dto]

        for data_transfer_object in dtos:
            if (
                data_transfer_object.__name__
                in framework_container.app_service_registry.kwargs
            ):
                raise DTOAlreadyRegistered(
                    f"The DTO: [{data_transfer_object.__name__} from {data_transfer_object.__module__}] is already registered"
                )

            framework_container.logger_bus.debug(
                f"Registering application service: [{data_transfer_object.__name__}]"
            )

            # --------------------------------------------------------------------
            # NEW: Register DTO class reference for introspection
            framework_container.dto_registry = providers.Dict(
                {
                    **{data_transfer_object.__name__: data_transfer_object},
                    **framework_container.dto_registry.kwargs,
                }
            )

            # --------------------------------------------------------------------
            # Register DTO to fn builder that will return the service instance
            framework_container.app_service_registry = providers.Dict(
                {
                    **{
                        data_transfer_object.__name__: providers.Factory(
                            decorated_class, framework_container.feature_bus
                        )
                    },
                    **framework_container.app_service_registry.kwargs,
                }
            )

            framework_container.app_service_bus.add_attributes(
                app_service_registry=framework_container.app_service_registry
            )

        return decorated_class

    return inner_fn
