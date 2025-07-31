from typing import Any, Callable, Dict, Optional, Type, overload

from sincpro_log.logger import LoggerProxy

from .exceptions import DTOAlreadyRegistered as DTOAlreadyRegistered
from .exceptions import UnknownDTOToExecute as UnknownDTOToExecute
from .sincpro_abstractions import ApplicationService as ApplicationService
from .sincpro_abstractions import Bus as Bus
from .sincpro_abstractions import DataTransferObject as DataTransferObject
from .sincpro_abstractions import Feature as Feature
from .sincpro_abstractions import TypeDTO as TypeDTO
from .sincpro_abstractions import TypeDTOResponse as TypeDTOResponse

class FeatureBus(Bus):
    """
    First layer of the framework, atomic features.

    The FeatureBus manages the execution of individual Features and maintains
    a registry mapping DTOs to their corresponding Feature handlers.
    """

    log_after_execution: bool
    feature_registry: Dict[str, Feature]
    handle_error: Optional[Callable[..., Any]]
    logger: LoggerProxy

    def __init__(self, logger_bus: LoggerProxy = ...) -> None: ...
    def register_feature(self, dto: Type[DataTransferObject], feature: Feature) -> bool:
        """Register a feature handler for a specific DTO type."""
        ...

    @overload
    def execute(self, dto: TypeDTO, return_type: Type[TypeDTOResponse]) -> TypeDTOResponse:
        """Execute a feature with specified return type for better IDE support."""
        ...

    @overload
    def execute(self, dto: TypeDTO) -> TypeDTOResponse | None:
        """Execute a feature without specifying return type."""
        ...

class ApplicationServiceBus(Bus):
    """
    Second layer of the framework, orchestration of features.

    The ApplicationServiceBus manages the execution of ApplicationServices and maintains
    a registry mapping DTOs to their corresponding ApplicationService handlers.
    """

    log_after_execution: bool
    app_service_registry: Dict[str, ApplicationService]
    handle_error: Optional[Callable[..., Any]]
    logger: LoggerProxy

    def __init__(self, logger_bus: LoggerProxy = ...) -> None: ...
    def register_app_service(
        self, dto: Type[DataTransferObject], app_service: ApplicationService
    ) -> bool:
        """Register an application service handler for a specific DTO type."""
        ...

    @overload
    def execute(self, dto: TypeDTO, return_type: Type[TypeDTOResponse]) -> TypeDTOResponse:
        """Execute an application service with specified return type for better IDE support."""
        ...

    @overload
    def execute(self, dto: TypeDTO) -> TypeDTOResponse | None:
        """Execute an application service without specifying return type."""
        ...

class FrameworkBus(Bus):
    """
    Facade bus to orchestrate the feature bus and app service bus.

    This component contains the following buses:
    - Feature bus: For executing individual Features
    - App service bus: For executing ApplicationServices (which contain the feature bus internally)

    The FrameworkBus automatically routes DTOs to the appropriate handler based on registration.
    """

    log_after_execution: bool
    feature_bus: FeatureBus
    app_service_bus: ApplicationServiceBus
    handle_error: Optional[Callable[..., Any]]
    logger: LoggerProxy
    dto_registry: Dict[str, Any]

    def __init__(
        self,
        feature_bus: FeatureBus,
        app_service_bus: ApplicationServiceBus,
        logger_bus: LoggerProxy = ...,
    ) -> None: ...
    @overload
    def execute(self, dto: TypeDTO, return_type: Type[TypeDTOResponse]) -> TypeDTOResponse:
        """
        Execute a DTO with specified return type for better IDE support.

        Automatically routes to either FeatureBus or ApplicationServiceBus
        based on DTO registration.
        """
        ...

    @overload
    def execute(self, dto: TypeDTO) -> TypeDTOResponse | None:
        """
        Execute a DTO without specifying return type.

        Automatically routes to either FeatureBus or ApplicationServiceBus
        based on DTO registration.
        """
        ...
