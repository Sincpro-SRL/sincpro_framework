from functools import partial
from typing import Any, Callable, Dict, Optional, Type

from sincpro_log.logger import LoggerProxy, create_logger

from . import ioc
from .bus import FrameworkBus
from .context import FrameworkContext
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

        # Instance-based context storage
        self._current_context: Dict[str, Any] = {}

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
            assert (
                self.bus is not None
            )  # Help mypy understand this is safe after the check above
            
            # Update context in all services before execution
            self._update_context_in_services()
            
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

    def context(self, context_attrs: Dict[str, Any]) -> FrameworkContext:
        """
        Create a context manager with the specified attributes
        
        Args:
            context_attrs: Dictionary of context attributes to set
            
        Returns:
            FrameworkContext instance ready to be used with 'with' statement
            
        Example:
            with app.context({"correlation_id": "123", "user_id": "456"}) as app_with_context:
                result = app_with_context(some_dto)
        """
        return FrameworkContext(self, context_attrs)

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

    def _get_current_context(self) -> Dict[str, Any]:
        """Get the current context for this framework instance"""
        return self._current_context.copy()
    
    def _set_current_context(self, context: Dict[str, Any]) -> None:
        """Set the current context for this framework instance"""
        self._current_context = context.copy()

    def _add_dependencies_provided_by_user(self):
        # No longer inject global context function - context is injected per service instance
        
        if "feature_registry" in self._sp_container.feature_bus.attributes:
            feature_registry = self._sp_container.feature_bus.attributes[
                "feature_registry"
            ].kwargs

            for dto, feature in feature_registry.items():
                # Inject context object into each feature instance
                self._inject_context_into_service(feature)
                feature.add_attributes(**self.dynamic_dep_registry)

        if "app_service_registry" in self._sp_container.app_service_bus.attributes:
            app_service_registry = self._sp_container.app_service_bus.attributes[
                "app_service_registry"
            ].kwargs

            for dto, app_service in app_service_registry.items():
                # Inject context object into each app service instance
                self._inject_context_into_service(app_service)
                app_service.add_attributes(**self.dynamic_dep_registry)

    def _inject_context_into_service(self, service):
        """Inject context object into a service instance"""
        from .context import ContextObject
        
        context_obj = ContextObject(self._current_context)
        if hasattr(service, '_set_context_object'):
            service._set_context_object(context_obj)

    def _update_context_in_services(self):
        """Update context in all registered services with current context"""
        if not self.was_initialized or self.bus is None:
            return
            
        from .context import ContextObject
        current_context_obj = ContextObject(self._current_context)
        
        # Update context in features
        if hasattr(self.bus, 'feature_bus') and hasattr(self.bus.feature_bus, 'feature_registry'):
            for feature in self.bus.feature_bus.feature_registry.values():
                if hasattr(feature, '_set_context_object'):
                    feature._set_context_object(current_context_obj)
        
        # Update context in app services  
        if hasattr(self.bus, 'app_service_bus') and hasattr(self.bus.app_service_bus, 'app_service_registry'):
            for app_service in self.bus.app_service_bus.app_service_registry.values():
                if hasattr(app_service, '_set_context_object'):
                    app_service._set_context_object(current_context_obj)

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
