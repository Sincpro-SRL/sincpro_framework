from typing import Dict, Union

from dependency_injector import containers, providers

from .bus import ApplicationServiceBus, FeatureBus, FrameworkBus
from .exceptions import DTOAlreadyRegistered
from .sincpro_abstractions import ApplicationService, Feature
from .sincpro_logger import logger

# ---------------------------------------------------------------------------------------------
# Container Definition
# ---------------------------------------------------------------------------------------------


class FrameworkContainer(containers.DeclarativeContainer):
    """Main container for the framework

    This container will initialize the
    main components of the framework at runtime.
    """

    bundled_context_name: str = providers.Object()

    injected_dependencies: dict = providers.Dict()

    # atomic layer
    feature_registry: Dict[str, Feature] = providers.Dict({})
    feature_bus: FeatureBus = providers.Factory(FeatureBus)

    # orchestration layer
    app_service_registry: Dict[str, ApplicationService] = providers.Dict({})
    app_service_bus: ApplicationServiceBus = providers.Factory(
        ApplicationServiceBus,
    )

    # Facade
    framework_bus: FrameworkBus = providers.Factory(
        FrameworkBus,
        feature_bus=feature_bus,
        app_service_bus=app_service_bus,
        bundled_context_name=bundled_context_name,
    )


# ---------------------------------------------------------------------------------------------
# Build processes
# ---------------------------------------------------------------------------------------------
def inject_feature_to_bus(framework_container: FrameworkContainer, dto: Union[str, list]):
    def inner_fn(decorated_class):
        dtos = dto
        if not isinstance(dto, list):
            dtos = [dto]

        for data_transfer_object in dtos:
            if data_transfer_object.__name__ in framework_container.feature_registry.kwargs:
                raise DTOAlreadyRegistered(
                    f"The DTO: [{data_transfer_object.__name__} from {data_transfer_object.__module__}] is already registered"
                    f" in the FEATURE LAYER, bundle context: [{framework_container.bundled_context_name}]"
                )

            logger.info(
                f"Registering feature: [{data_transfer_object.__name__}]",
                bundled_context=framework_container.bundled_context_name,
            )

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
                    f" in the APP SERVICE LAYER, bundle context: [{framework_container.bundled_context_name}]"
                )

            logger.info(
                f"Registering application service: [{data_transfer_object.__name__}]",
                bundled_context=framework_container.bundled_context_name,
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
