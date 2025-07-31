from typing import Any, Callable, Dict, Type, TypeVar, overload

from _typeshed import Incomplete
from sincpro_log.logger import LoggerProxy

from . import ioc as ioc
from .bus import FrameworkBus as FrameworkBus
from .context.mixin import ContextMixin
from .exceptions import DependencyAlreadyRegistered as DependencyAlreadyRegistered
from .exceptions import SincproFrameworkNotBuilt as SincproFrameworkNotBuilt
from .middleware import Middleware, MiddlewarePipeline
from .sincpro_abstractions import ApplicationService, DataTransferObject, Feature
from .sincpro_abstractions import TypeDTO as TypeDTO
from .sincpro_abstractions import TypeDTOResponse as TypeDTOResponse
from .sincpro_logger import create_logger as create_logger

# Type alias for decorator functions
DecoratorFunction = Callable[[Type], Type]

class UseFramework(ContextMixin):
    """
    Main class to use the framework, this is the main entry point to configure the framework.

    The UseFramework class provides:
    - Dependency injection registration via add_dependency()
    - Feature and ApplicationService decorators
    - Middleware registration
    - Error handler configuration
    - Framework execution as a callable

    Example:
        # Setup
        framework = UseFramework("my_context")
        framework.add_dependency("database", db_instance)

        # Register handlers
        @framework.feature(MyDTO)
        class MyFeature(Feature): ...

        # Execute
        result = framework(MyDTO(param="value"), MyResponseDTO)
    """

    log_after_execution: bool
    log_app_services: bool
    log_features: bool

    # Decorators with better typing
    feature: Callable[[Type[DataTransferObject]], DecoratorFunction]
    app_service: Callable[[Type[DataTransferObject]], DecoratorFunction]

    middleware_pipeline: MiddlewarePipeline
    dynamic_dep_registry: Dict[str, Any]
    global_error_handler: Callable[..., Any] | None
    feature_error_handler: Callable[..., Any] | None
    app_service_error_handler: Callable[..., Any] | None
    was_initialized: bool
    bus: FrameworkBus | None

    def __init__(
        self,
        bundled_context_name: str = "sincpro_framework",
        log_after_execution: bool = True,
        log_app_services: bool = True,
        log_features: bool = True,
    ) -> None:
        """
        Initialize the framework.

        Args:
            bundled_context_name: Name of the logger context
            log_after_execution: Enable/disable execution logging
            log_app_services: Enable/disable application service logging
            log_features: Enable/disable feature logging
        """
        ...
    # Improved overloads for framework execution
    @overload
    def __call__(self, dto: TypeDTO, return_type: Type[TypeDTOResponse]) -> TypeDTOResponse:
        """
        Execute a DTO with specified return type for better IDE support.

        Args:
            dto: The Data Transfer Object to execute
            return_type: The expected response DTO type

        Returns:
            The response DTO of the specified type
        """
        ...

    @overload
    def __call__(self, dto: TypeDTO) -> TypeDTOResponse | None:
        """
        Execute a DTO without specifying return type.

        Args:
            dto: The Data Transfer Object to execute

        Returns:
            The response DTO or None if no response
        """
        ...

    def build_root_bus(self) -> None:
        """Build the root bus with the dependencies provided by the user."""
        ...

    def add_dependency(self, name: str, dep: Any) -> None:
        """
        Add a dependency to the framework.

        The dependency will be injected as an attribute into all Features and ApplicationServices.

        Args:
            name: The attribute name for the dependency
            dep: The dependency instance, function, or class

        Raises:
            DependencyAlreadyRegistered: If the dependency name is already registered
        """
        ...

    def add_middleware(self, middleware: Middleware) -> None:
        """
        Add middleware function to the execution pipeline.

        Args:
            middleware: The middleware function to add
        """
        ...

    def add_global_error_handler(self, handler: Callable[..., Any]) -> None:
        """
        Add a global error handler for all framework errors.

        Args:
            handler: A callable that handles exceptions
        """
        ...

    def add_feature_error_handler(self, handler: Callable[..., Any]) -> None:
        """
        Add an error handler specifically for Feature errors.

        Args:
            handler: A callable that handles Feature exceptions
        """
        ...

    def add_app_service_error_handler(self, handler: Callable[..., Any]) -> None:
        """
        Add an error handler specifically for ApplicationService errors.

        Args:
            handler: A callable that handles ApplicationService exceptions
        """
        ...

    @property
    def logger(self) -> LoggerProxy:
        """Get the framework logger instance."""
        ...
