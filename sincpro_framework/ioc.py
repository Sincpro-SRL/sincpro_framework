"""Inversion of Control (IoC) container for the SincPro Framework"""

import inspect
from enum import Enum
from functools import wraps
from typing import TYPE_CHECKING, Callable, TypeAlias, TypeVar, get_type_hints

# dependency-injector 4.49 re-enables the GIL when imported on a free-threaded Python 3.14t, until
# it declares free-threading support. Regular 3.12-3.14 are unaffected.
from dependency_injector import containers, providers
from dependency_injector.providers import Dict, Factory, Object, Singleton
from sincpro_log.logger import LoggerProxy

from sincpro_framework.common.naming import registered_name

from .bus import ApplicationServiceBus, FeatureBus, FrameworkBus
from .exceptions import DTOAlreadyRegistered, UnknownDTOToExecute
from .observability import Observability
from .sincpro_abstractions import DataTransferObject

if TYPE_CHECKING:
    from _typeshed import DataclassInstance

T = TypeVar("T", bound=type)
DTOClass: TypeAlias = "type[DataTransferObject] | type[DataclassInstance]"
DTORegistration: TypeAlias = "DTOClass | list[DTOClass]"

# ---------------------------------------------------------------------------------------------
# Container Definition
# ---------------------------------------------------------------------------------------------


class FrameworkContainer(containers.DeclarativeContainer):
    """Main container for the framework

    This container will initialize the
    main components of the framework at runtime.
    """

    logger_bus: Object[LoggerProxy] = providers.Object()
    observability: Object[Observability] = providers.Object()
    injected_dependencies: Dict = providers.Dict()
    dto_registry: Dict = Dict({})
    replacements: Dict = providers.Dict({})
    """DTO → the handlers it replaced, oldest first, as `module.Class`."""

    # atomic layer
    feature_registry: Dict = providers.Dict({})
    feature_bus: Singleton[FeatureBus] = providers.Singleton(
        FeatureBus, logger_bus, observability  # type: ignore[arg-type]
    )

    # orchestration layer
    app_service_registry: Dict = providers.Dict({})
    app_service_bus: Singleton[ApplicationServiceBus] = providers.Singleton(
        ApplicationServiceBus, logger_bus, observability  # type: ignore[arg-type]
    )

    # Facade
    framework_bus: Factory[FrameworkBus] = providers.Factory(
        FrameworkBus,
        feature_bus=feature_bus,
        app_service_bus=app_service_bus,
        logger_bus=logger_bus,  # type: ignore[arg-type]
        observability=observability,  # type: ignore[arg-type]
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
    dto: DTORegistration,
    decorated_class: type,
    context: str,
) -> None:
    """Register a service (feature or app_service) to the framework container.

    1. Refused before it is registered:
       1.1 a DTO this layer already answers;
       1.2 two use cases of the bus with one class name — every wire and workflow publishes a
           use case by its name, so only one of them would reach a client;
       1.3 two DTOs under one identity (`registered_name`) — the remote bus, the queues and the
           Subscriber route by it.
    2. Final: the DTO in `dto_registry` by its identity, its handler in the layer's registry.
    """
    layer = (
        framework_container.feature_registry
        if service_type == ServiceType.FEATURE
        else framework_container.app_service_registry
    )
    published = {
        one.__name__: one
        for one in (
            *framework_container.feature_registry.kwargs,
            *framework_container.app_service_registry.kwargs,
        )
    }

    for data_transfer_object in dto if isinstance(dto, list) else [dto]:
        dto_name = data_transfer_object.__name__
        identity = registered_name(data_transfer_object, context)

        if data_transfer_object in layer.kwargs:
            answered_as = (
                "a feature"
                if service_type == ServiceType.FEATURE
                else "an application service"
            )
            raise DTOAlreadyRegistered(
                f"The DTO: [{dto_name} from {data_transfer_object.__module__}] is already "
                f"registered as {answered_as}"
            )

        homonym = published.get(dto_name)
        if homonym is not None and homonym is not data_transfer_object:
            raise DTOAlreadyRegistered(
                f"Two use cases of this bus are named {dto_name}: "
                f"{homonym.__module__}.{homonym.__qualname__} and "
                f"{data_transfer_object.__module__}.{data_transfer_object.__qualname__}. A bus "
                "publishes a use case by its name on every wire and in every workflow, so only "
                "one of them would reach a client: rename one of the classes"
            )

        existing = framework_container.dto_registry.kwargs.get(identity)
        if existing is not None and existing is not data_transfer_object:
            raise DTOAlreadyRegistered(
                f"The message identity [{identity}] is already used by "
                f"{existing.__module__}.{existing.__qualname__}; "
                f"{data_transfer_object.__module__}.{data_transfer_object.__qualname__} needs a "
                "different name to be routed by identity (remote bus, queues, Subscriber)"
            )

        framework_container.logger_bus.debug(f"Registering {service_type}: [{identity}]")  # type: ignore[union-attr]
        published[dto_name] = data_transfer_object

        framework_container.dto_registry = providers.Dict(
            {**{identity: data_transfer_object}, **framework_container.dto_registry.kwargs}
        )

        match service_type:
            case ServiceType.FEATURE:
                framework_container.feature_registry = providers.Dict(
                    {
                        **{data_transfer_object: providers.Factory(decorated_class)},
                        **framework_container.feature_registry.kwargs,
                    }
                )
                framework_container.feature_bus.add_attributes(
                    feature_registry=framework_container.feature_registry
                )

            case ServiceType.APP_SERVICE:
                framework_container.app_service_registry = providers.Dict(
                    {
                        **{
                            data_transfer_object: providers.Factory(
                                decorated_class, framework_container.feature_bus
                            )
                        },
                        **framework_container.app_service_registry.kwargs,
                    }
                )
                framework_container.app_service_bus.add_attributes(
                    app_service_registry=framework_container.app_service_registry
                )


def _replace_service(
    framework_container: FrameworkContainer,
    service_type: ServiceType,
    dto: DTORegistration,
    replacement: type,
    replaced: type,
) -> None:
    """The handler registered for each DTO becomes `replacement`, and only when it is `replaced`
    — so a replacement cannot land on the wrong handler, before the core registered its own, or
    on top of another replacement it does not name.

    1. Every DTO is answered by `replaced` — checked for all before any is swapped, so a refused
       replacement leaves every handler as it was.
    2. The replacement keeps the replaced handler's response contract (Liskov): when both
       declare a response class, the replacement's is that class or a narrower one.
    3. Final: swap the factories and record what was replaced, for the build log and
       introspection.
    """
    registry = (
        framework_container.feature_registry
        if service_type == ServiceType.FEATURE
        else framework_container.app_service_registry
    )
    data_transfer_objects = dto if isinstance(dto, list) else [dto]
    replaced_as = f"{replaced.__module__}.{replaced.__qualname__}"

    for data_transfer_object in data_transfer_objects:
        current = registry.kwargs.get(data_transfer_object)
        if current is None:
            raise UnknownDTOToExecute(
                f"{replacement.__name__} replaces {replaced.__name__}, but nothing answers "
                f"{data_transfer_object.__name__} yet — register the core handler first"
            )
        answering = current.provides
        if answering is replaced:
            continue
        if replaced_as in framework_container.replacements.kwargs.get(
            data_transfer_object, ()
        ):
            raise DTOAlreadyRegistered(
                f"{replacement.__name__} replaces {replaced.__name__}, but "
                f"{answering.__name__} already did — replace {answering.__name__} or remove it"
            )
        raise DTOAlreadyRegistered(
            f"{replacement.__name__} replaces {replaced.__name__}, but "
            f"{data_transfer_object.__name__} is handled by {answering.__name__}"
        )

    try:
        new = get_type_hints(replacement.execute).get("return")
        old = get_type_hints(replaced.execute).get("return")
    except NameError:
        new = old = None
    if isinstance(new, type) and isinstance(old, type) and not issubclass(new, old):
        raise TypeError(
            f"{replacement.__name__} answers {new.__name__}, but {replaced.__name__} answers "
            f"{old.__name__} — a replacement keeps the contract of what it replaces"
        )

    factory = (
        providers.Factory(replacement)
        if service_type == ServiceType.FEATURE
        else providers.Factory(replacement, framework_container.feature_bus)
    )
    updated = providers.Dict(
        {**registry.kwargs, **{one: factory for one in data_transfer_objects}}
    )
    if service_type == ServiceType.FEATURE:
        framework_container.feature_registry = updated
        framework_container.feature_bus.add_attributes(feature_registry=updated)
    else:
        framework_container.app_service_registry = updated
        framework_container.app_service_bus.add_attributes(app_service_registry=updated)

    replacements = framework_container.replacements.kwargs
    framework_container.replacements = providers.Dict(
        {
            **replacements,
            **{
                one: (*replacements.get(one, ()), replaced_as)
                for one in data_transfer_objects
            },
        }
    )


def _registering(
    framework_container: FrameworkContainer,
    service_type: ServiceType,
    dto: DTORegistration,
    replaces: type | None,
    context: str,
) -> Callable[[T], T]:
    """The decorator both layers share. The bus calls `execute` and never awaits it, so an
    `async def execute` is refused: it would answer an unawaited coroutine, its work never run.
    """

    def decorator(decorated_class: T) -> T:
        if inspect.iscoroutinefunction(decorated_class.execute):
            raise TypeError(
                f"{decorated_class.__name__} declares async def execute, and the bus is "
                "synchronous: declare it def execute — an async caller reaches it with "
                "bus.get_async_bus(), which runs it off the event loop"
            )
        if replaces is None:
            _register_service(
                framework_container, service_type, dto, decorated_class, context
            )
        else:
            _replace_service(
                framework_container, service_type, dto, decorated_class, replaces
            )
        return decorated_class

    return decorator


def inject_feature_to_bus(
    framework_container: FrameworkContainer,
    dto: DTORegistration,
    replaces: type | None = None,
    context: str = "",
) -> Callable[[T], T]:
    """Decorator to register a feature to the framework bus

    Args:
        framework_container: The IoC container instance
        dto: DTO or list of DTOs to register with the feature
        replaces: The Feature currently registered for them, when this one answers instead
        context: The bus's name — what each DTO's identity is namespaced by

    Returns:
        Decorated class with feature registration functionality
    """
    return wraps(inject_feature_to_bus)(
        _registering(framework_container, ServiceType.FEATURE, dto, replaces, context)
    )


def inject_app_service_to_bus(
    framework_container: FrameworkContainer,
    dto: DTORegistration,
    replaces: type | None = None,
    context: str = "",
) -> Callable[[T], T]:
    """Decorator to register an application service to the framework bus

    Args:
        framework_container: The IoC container instance
        dto: DTO or list of DTOs to register with the application service
        replaces: The ApplicationService currently registered for them, when this one answers
            instead
        context: The bus's name — what each DTO's identity is namespaced by

    Returns:
        Decorated class with app service registration functionality
    """
    return wraps(inject_app_service_to_bus)(
        _registering(framework_container, ServiceType.APP_SERVICE, dto, replaces, context)
    )
