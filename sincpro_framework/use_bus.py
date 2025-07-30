from functools import partial
from typing import Any, Callable, Dict, Optional, Type

from sincpro_log.logger import LoggerProxy, create_logger

from . import ioc
from .bus import FrameworkBus
from .context_manager import ContextManager, ContextConfig, create_context_manager
from .exceptions import DependencyAlreadyRegistered, SincproFrameworkNotBuilt
from .middleware import Middleware, MiddlewarePipeline
from .sincpro_abstractions import TypeDTO, TypeDTOResponse


class UseFramework:
    """
    Main class to use the framework, this is the main entry point to configure the framework
    """

    def __init__(
        self,
        bundled_context_name: str = "sincpro_framework",
        log_after_execution: bool = True,
        log_app_services: bool = True,
        log_features: bool = True,
    ):
        """Initialize the framework

        Args:
            bundled_context_name (str): Name of the logger
            log_after_execution (bool): Log after execution if False, All logs will be disabled
            log_app_services (bool): Log app services, If log_after_execution is False, this will be disabled
            log_features (bool): Log features, If log_after_execution is False, this will be disabled
        """
        # Logger
        self._is_logger_configured: bool = False
        self._logger_name: str = bundled_context_name
        self._logger: LoggerProxy | None = None
        self.log_after_execution: bool = log_after_execution
        self.log_app_services: bool = log_app_services
        self.log_features: bool = log_features

        # Container
        self._sp_container = ioc.FrameworkContainer(logger_bus=self.logger)
        self._sp_container.logger_bus = self.logger

        # Decorators
        self.feature = partial(ioc.inject_feature_to_bus, self._sp_container)
        self.app_service = partial(ioc.inject_app_service_to_bus, self._sp_container)

        # Registry for dynamic dep injection
        self.dynamic_dep_registry: Dict[str, Any] = dict()

        # Error handlers
        self.global_error_handler: Optional[Callable] = None
        self.feature_error_handler: Optional[Callable] = None
        self.app_service_error_handler: Optional[Callable] = None

        # Middleware pipeline
        self.middleware_pipeline = MiddlewarePipeline()

        # Context manager support
        self._context_manager_instance: Optional[ContextManager] = None
        self._context_config: Optional[ContextConfig] = None

        self.was_initialized: bool = False
        self.bus: FrameworkBus | None = None

    def __call__(
        self, dto: TypeDTO, return_type: Type[TypeDTOResponse] | None = None
    ) -> TypeDTOResponse | None:
        """
        Main function to execute the framework
        :param dto:
        :return: Any response
        """
        if not self.was_initialized:
            self.build_root_bus()

        if self.bus is None:
            raise SincproFrameworkNotBuilt(
                "Check the decorators are rigistering the features and app services, check the imports of each "
                "feature and app service"
            )

        # Execute with middleware pipeline
        def executor(processed_dto, **exec_kwargs) -> TypeDTOResponse | None:
            return self.bus.execute(processed_dto)

        return self.middleware_pipeline.execute(dto, executor, return_type=return_type)

    def build_root_bus(self):
        """Build the root bus with the dependencies provided by the user"""
        self._add_dependencies_provided_by_user()
        self._add_error_handlers_provided_by_user()
        self.was_initialized = True
        dto_registry = self._sp_container.dto_registry()

        self.bus: FrameworkBus = self._sp_container.framework_bus()

        # Set the loggers
        self.bus.log_after_execution = self.log_after_execution
        self.bus.feature_bus.log_after_execution = (
            self.log_after_execution and self.log_features
        )
        self.bus.app_service_bus.log_after_execution = (
            self.log_after_execution and self.log_app_services
        )

        # Set the DTO registry Tricky way but it works
        self.bus.dto_registry = dto_registry

    def add_dependency(self, name, dep: Any):
        """
        Add a dependency to the framework where
        The Feature and App Service have as attribute
        """
        if name in self.dynamic_dep_registry:
            raise DependencyAlreadyRegistered(f"The dependency {name} is already injected")
        self.dynamic_dep_registry[name] = dep

    def add_middleware(self, middleware: Middleware):
        """Add middleware function to the execution pipeline"""
        self.middleware_pipeline.add_middleware(middleware)

    def configure_context_manager(
        self,
        default_attrs: Optional[Dict[str, Any]] = None,
        allowed_keys: Optional[set] = None,
        validate_types: bool = True,
        max_key_length: int = 100,
        max_value_length: int = 1000
    ):
        """
        Configure the context manager for this framework instance
        
        Args:
            default_attrs: Default attributes to include in every context
            allowed_keys: Set of allowed context keys (None means all keys allowed)
            validate_types: Whether to validate context value types
            max_key_length: Maximum length for context keys
            max_value_length: Maximum length for string context values
        """
        self._context_config = ContextConfig(
            default_attrs=default_attrs,
            allowed_keys=set(allowed_keys) if allowed_keys else None,
            validate_types=validate_types,
            max_key_length=max_key_length,
            max_value_length=max_value_length
        )

    def context(self, context_attrs: Dict[str, Any]) -> ContextManager:
        """
        Create a context manager with the specified attributes
        
        Args:
            context_attrs: Dictionary of context attributes to set
            
        Returns:
            ContextManager instance ready to be used with 'with' statement
            
        Example:
            with framework.context({"correlation_id": "123", "user_id": "456"}):
                result = framework(some_dto)
        """
        return create_context_manager(context_attrs, self._context_config)

    @property
    def context_manager(self) -> Optional[ContextManager]:
        """Get the current context manager instance (for advanced usage)"""
        return self._context_manager_instance

    @context_manager.setter
    def context_manager(self, manager: ContextManager):
        """Set a custom context manager instance"""
        if not isinstance(manager, ContextManager):
            raise TypeError("context_manager must be an instance of ContextManager")
        self._context_manager_instance = manager

    def add_global_error_handler(self, handler: Callable):
        if not callable(handler):
            raise TypeError("The handler must be a callable")
        self.global_error_handler = handler

    def add_feature_error_handler(self, handler: Callable):
        if not callable(handler):
            raise TypeError("The handler must be a callable")
        self.feature_error_handler = handler

    def add_app_service_error_handler(self, handler: Callable):
        if not callable(handler):
            raise TypeError("The handler must be a callable")
        self.app_service_error_handler = handler

    def _add_dependencies_provided_by_user(self):
        # Add current context to dynamic dependencies
        from .context_manager import get_current_context
        self.dynamic_dep_registry['framework_context'] = get_current_context

        if "feature_registry" in self._sp_container.feature_bus.attributes:
            feature_registry = self._sp_container.feature_bus.attributes[
                "feature_registry"
            ].kwargs

            for dto, feature in feature_registry.items():
                feature.add_attributes(**self.dynamic_dep_registry)

        if "app_service_registry" in self._sp_container.app_service_bus.attributes:
            app_service_registry = self._sp_container.app_service_bus.attributes[
                "app_service_registry"
            ].kwargs

            for dto, app_service in app_service_registry.items():
                app_service.add_attributes(**self.dynamic_dep_registry)

    def _add_error_handlers_provided_by_user(self):
        if self.global_error_handler:
            self._sp_container.framework_bus.add_attributes(
                handle_error=self.global_error_handler
            )

        if self.feature_error_handler:
            self._sp_container.feature_bus.add_attributes(
                handle_error=self.feature_error_handler
            )

        if self.app_service_error_handler:
            self._sp_container.app_service_bus.add_attributes(
                handle_error=self.app_service_error_handler
            )

    def _execute_with_middleware(
        self,
        dto: TypeDTO,
        executor: Callable,
        return_type: Type[TypeDTOResponse] | None = None,
    ) -> TypeDTOResponse | None:
        """
        Execute the DTO with the middleware pipeline
        """
        return self.middleware_pipeline.execute(dto, executor, return_type=return_type)

    @property
    def logger(self) -> LoggerProxy:
        """Get bundle context logger"""
        if not self._is_logger_configured:
            self._logger = create_logger(self._logger_name)
            self._is_logger_configured = True
        return self._logger
