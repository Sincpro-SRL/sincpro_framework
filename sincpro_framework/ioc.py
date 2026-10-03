"""Inversion of Control (IoC) container for the SincPro Framework"""

import inspect
import sys
from enum import Enum
from functools import wraps
from typing import TYPE_CHECKING, Any, Callable, TypeAlias, TypeVar, get_type_hints

if TYPE_CHECKING:
    from _typeshed import DataclassInstance

    from .ddd.events import DomainEvent

# PYTHON 3.14 FREE-THREADING: dependency-injector 4.49.1 ships an abi3 wheel
# (works fine on regular 3.14) but has not declared free-threading support.
# Importing it on a `python3.14t` (free-threaded) interpreter makes CPython
# print RuntimeWarning "The global interpreter lock (GIL) has been enabled to
# load module 'dependency_injector.containers'..." and silently re-enable the
# GIL for the whole process — verified on 3.14.7t. Not a bug in this codebase;
# nothing to fix here until dependency-injector itself declares
# Py_mod_gil/free-threading support. Regular (GIL) 3.12/3.13/3.14 unaffected.
from dependency_injector import containers, providers
from dependency_injector.providers import Dict, Factory, Object, Singleton
from sincpro_log.logger import LoggerProxy
from typing_extensions import TypeIs

# Type variable for decorator return type
T = TypeVar("T", bound=type)

from .bus import ApplicationServiceBus, FeatureBus, FrameworkBus
from .exceptions import DTOAlreadyRegistered, UnknownDTOToExecute
from .observability import Observability
from .sincpro_abstractions import DataTransferObject

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


def _is_domain_event(cls: type) -> "TypeIs[type[DomainEvent]]":
    events_module = sys.modules.get("sincpro_framework.ddd.events")
    return events_module is not None and issubclass(cls, events_module.DomainEvent)


def registered_name(dto_type: type) -> str:
    """The name a bus registers a DTO under — the same in every service running the code: an
    event by its wire `name`, any other DTO by `module.qualname`."""
    if _is_domain_event(dto_type):
        return dto_type.name
    return f"{dto_type.__module__}.{dto_type.__qualname__}"


# ---------------------------------------------------------------------------------------------
# Build processes
# ---------------------------------------------------------------------------------------------


def _register_service(
    framework_container: FrameworkContainer,
    service_type: ServiceType,
    dto: DTORegistration,
    decorated_class: type,
) -> None:
    """Register a service (feature or app_service) to the framework container"""

    # Normalize DTOs to list
    dto_list = dto if isinstance(dto, list) else [dto]

    for data_transfer_object in dto_list:
        dto_name = data_transfer_object.__name__
        registry_key = registered_name(data_transfer_object)
        log_label = (
            f'{dto_name}(name="{registry_key}")'
            if _is_domain_event(data_transfer_object)
            else dto_name
        )

        if (
            service_type == ServiceType.FEATURE
            and data_transfer_object in framework_container.feature_registry.kwargs
        ):
            raise DTOAlreadyRegistered(
                f"The DTO: [{dto_name} from {data_transfer_object.__module__}] is already registered as a feature"
            )

        if (
            service_type == ServiceType.APP_SERVICE
            and data_transfer_object in framework_container.app_service_registry.kwargs
        ):
            raise DTOAlreadyRegistered(
                f"The DTO: [{dto_name} from {data_transfer_object.__module__}] is already registered as an application service"
            )

        existing = framework_container.dto_registry.kwargs.get(registry_key)
        if existing is not None and existing is not data_transfer_object:
            raise DTOAlreadyRegistered(
                f"The DTO name [{registry_key}] is already used by {existing.__module__}.{existing.__qualname__}; "
                f"{data_transfer_object.__module__}.{data_transfer_object.__qualname__} needs a different name "
                "to be introspected and routed by name (Subscriber, BackgroundQueue)"
            )

        # Log registration
        framework_container.logger_bus.debug(f"Registering {service_type}: [{log_label}]")  # type: ignore[union-attr]

        framework_container.dto_registry = providers.Dict(
            {
                **{registry_key: data_transfer_object},
                **framework_container.dto_registry.kwargs,
            }
        )

        # Update container registry and bus attributes
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


def _qualified(cls: type) -> str:
    return f"{cls.__module__}.{cls.__qualname__}"


def _declared_response(handler: type) -> Any:
    try:
        return get_type_hints(handler.execute).get("return")
    except NameError:
        return None


def _refuse_an_incompatible_response(replacement: type, replaced: type) -> None:
    """Context: a replacement keeps the contract of the handler it replaces (Liskov) — when both
    declare a response class, the replacement's is that class or a narrower one."""
    new, old = _declared_response(replacement), _declared_response(replaced)
    if isinstance(new, type) and isinstance(old, type) and not issubclass(new, old):
        raise TypeError(
            f"{replacement.__name__} answers {new.__name__}, but {replaced.__name__} answers "
            f"{old.__name__} — a replacement keeps the contract of what it replaces"
        )


def _refuse_a_wrong_replacement(
    framework_container: FrameworkContainer,
    registry: providers.Dict,
    data_transfer_object: type,
    replacement: type,
    replaced: type,
) -> None:
    name = data_transfer_object.__name__
    current = registry.kwargs.get(data_transfer_object)
    if current is None:
        raise UnknownDTOToExecute(
            f"{replacement.__name__} replaces {replaced.__name__}, but nothing answers {name} "
            "yet — register the core handler first"
        )
    answering = current.provides
    if answering is replaced:
        return
    chain: tuple[str, ...] = framework_container.replacements.kwargs.get(
        data_transfer_object, ()
    )
    if _qualified(replaced) in chain:
        raise DTOAlreadyRegistered(
            f"{replacement.__name__} replaces {replaced.__name__}, but "
            f"{answering.__name__} already did — replace {answering.__name__} or remove it"
        )
    raise DTOAlreadyRegistered(
        f"{replacement.__name__} replaces {replaced.__name__}, but {name} is handled by "
        f"{answering.__name__}"
    )


def _replace_service(
    framework_container: FrameworkContainer,
    service_type: ServiceType,
    dto: DTORegistration,
    replacement: type,
    replaced: type,
) -> None:
    """Context: the handler registered for each DTO becomes `replacement`, and only when it is
    `replaced` — so a replacement cannot land on the wrong handler, before the core registered
    its own, or on top of another replacement it does not name.

    1. Every DTO is answered by `replaced` — checked for all before any is swapped, so a refused
       replacement leaves every handler as it was.
    2. The replacement keeps the replaced handler's response contract.
    3. Final: swap the factories and record what was replaced, for the build log and
       introspection.
    """
    registry = (
        framework_container.feature_registry
        if service_type == ServiceType.FEATURE
        else framework_container.app_service_registry
    )
    data_transfer_objects = dto if isinstance(dto, list) else [dto]
    for data_transfer_object in data_transfer_objects:
        _refuse_a_wrong_replacement(
            framework_container, registry, data_transfer_object, replacement, replaced
        )
    _refuse_an_incompatible_response(replacement, replaced)

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
                one: (*replacements.get(one, ()), _qualified(replaced))
                for one in data_transfer_objects
            },
        }
    )


def names_of(dto: DTORegistration) -> str:
    return ", ".join(one.__name__ for one in (dto if isinstance(dto, list) else [dto]))


def _refuse_an_async_execute(decorated_class: type) -> None:
    """Context: the bus calls `execute` and does not await it, so an `async def execute` would
    answer an unawaited coroutine — its work never run, its errors never seen by a handler."""
    if inspect.iscoroutinefunction(decorated_class.execute):
        raise TypeError(
            f"{decorated_class.__name__} declares async def execute, and the bus is synchronous: "
            "declare it def execute — an async caller reaches it with bus.get_async_bus(), "
            "which runs it off the event loop"
        )


def inject_feature_to_bus(
    framework_container: FrameworkContainer,
    dto: DTORegistration,
    replaces: type | None = None,
) -> Callable[[T], T]:
    """Decorator to register a feature to the framework bus

    Args:
        framework_container: The IoC container instance
        dto: DTO or list of DTOs to register with the feature
        replaces: The Feature currently registered for them, when this one answers instead

    Returns:
        Decorated class with feature registration functionality
    """

    @wraps(inject_feature_to_bus)
    def decorator(decorated_class: T) -> T:
        _refuse_an_async_execute(decorated_class)
        if replaces is None:
            _register_service(framework_container, ServiceType.FEATURE, dto, decorated_class)
        else:
            _replace_service(
                framework_container, ServiceType.FEATURE, dto, decorated_class, replaces
            )
        return decorated_class

    return decorator


def inject_app_service_to_bus(
    framework_container: FrameworkContainer,
    dto: DTORegistration,
    replaces: type | None = None,
) -> Callable[[T], T]:
    """Decorator to register an application service to the framework bus

    Args:
        framework_container: The IoC container instance
        dto: DTO or list of DTOs to register with the application service
        replaces: The ApplicationService currently registered for them, when this one answers
            instead

    Returns:
        Decorated class with app service registration functionality
    """

    @wraps(inject_app_service_to_bus)
    def decorator(decorated_class: T) -> T:
        _refuse_an_async_execute(decorated_class)
        if replaces is None:
            _register_service(
                framework_container, ServiceType.APP_SERVICE, dto, decorated_class
            )
        else:
            _replace_service(
                framework_container, ServiceType.APP_SERVICE, dto, decorated_class, replaces
            )
        return decorated_class

    return decorator
