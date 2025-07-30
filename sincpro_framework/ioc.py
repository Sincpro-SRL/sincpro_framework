"""Inversion of Control (IoC) container for the SincPro Framework"""

from enum import Enum
from functools import wraps
from typing import Any, Callable, TypeVar, Union

from dependency_injector import containers, providers
from dependency_injector.providers import Dict, Factory, Object, Singleton
from sincpro_log.logger import LoggerProxy

# Type variable for decorator return type
T = TypeVar("T", bound=type)

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
    dto_registry: Dict = Dict({})

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
        logger_bus=logger_bus,
    )


# ---------------------------------------------------------------------------------------------
# Service Types
# ---------------------------------------------------------------------------------------------


class ServiceType(Enum):
    FEATURE = "feature"
    APP_SERVICE = "app_service"


# ---------------------------------------------------------------------------------------------
# Build processes
# ---------------------------------------------------------------------------------------------


def _register_service(
    framework_container: FrameworkContainer,
    service_type: ServiceType,
    dto: Union[str, list, TypeDTO, Any],
    decorated_class: type,
) -> None:
    """Register a service (feature or app_service) to the framework container"""

    # Normalize DTOs to list
    dto_list = dto if isinstance(dto, list) else [dto]

    match service_type:
        case ServiceType.FEATURE:
            current_service_registry = framework_container.feature_registry
            target_bus = framework_container.feature_bus
            service_name = "feature"
            factory_dependencies = []
            registry_attribute_name = "feature_registry"

        case ServiceType.APP_SERVICE:
            current_service_registry = framework_container.app_service_registry
            target_bus = framework_container.app_service_bus
            service_name = "application service"
            factory_dependencies = [framework_container.feature_bus]
            registry_attribute_name = "app_service_registry"

    for data_transfer_object in dto_list:
        dto_name = data_transfer_object.__name__

        # Check if DTO is already registered
        if dto_name in current_service_registry.kwargs:
            raise DTOAlreadyRegistered(
                f"The DTO: [{dto_name} from {data_transfer_object.__module__}] is already registered"
            )

        # Log registration
        framework_container.logger_bus.debug(f"Registering {service_name}: [{dto_name}]")

        dto_registry = framework_container.dto_registry.kwargs

        # Update DTO registry with new DTO
        updated_dto_registry = providers.Dict(
            {**{dto_name: data_transfer_object}, **dto_registry}
        )

        framework_container.dto_registry = updated_dto_registry

        # Create updated service registry with new service factory
        updated_service_registry = providers.Dict(
            {
                **{dto_name: providers.Factory(decorated_class, *factory_dependencies)},
                **current_service_registry.kwargs.copy(),
            }
        )

        # Update container registry and bus attributes
        if service_type == ServiceType.FEATURE:
            framework_container.feature_registry = updated_service_registry
        else:
            framework_container.app_service_registry = updated_service_registry

        target_bus.add_attributes(**{registry_attribute_name: updated_service_registry})


def inject_feature_to_bus(
    framework_container: FrameworkContainer, dto: Union[str, list, TypeDTO, Any]
) -> Callable[[T], T]:
    """Decorator to register a feature to the framework bus

    Args:
        framework_container: The IoC container instance
        dto: DTO or list of DTOs to register with the feature

    Returns:
        Decorated class with feature registration functionality
    """

    @wraps(inject_feature_to_bus)
    def decorator(decorated_class: T) -> T:
        _register_service(framework_container, ServiceType.FEATURE, dto, decorated_class)
        return decorated_class

    return decorator


def inject_app_service_to_bus(
    framework_container: FrameworkContainer, dto: Union[str, list]
) -> Callable[[T], T]:
    """Decorator to register an application service to the framework bus

    Args:
        framework_container: The IoC container instance
        dto: DTO or list of DTOs to register with the application service

    Returns:
        Decorated class with app service registration functionality
    """

    @wraps(inject_app_service_to_bus)
    def decorator(decorated_class: T) -> T:
        _register_service(framework_container, ServiceType.APP_SERVICE, dto, decorated_class)
        return decorated_class

    return decorator
