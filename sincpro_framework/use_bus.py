from functools import partial
from typing import Any, Callable, Dict, Optional, Type
import time

from sincpro_log.logger import LoggerProxy, create_logger

from . import ioc
from .bus import FrameworkBus
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

        # Observability
        self.observability_enabled: bool = False
        self._tracer = None
        
        self.was_initialized: bool = False
        self.bus: FrameworkBus | None = None

    def enable_observability(self, service_name: str = None, jaeger_endpoint: str = None):
        """Enable comprehensive observability for the framework.
        
        Args:
            service_name (str): Name of the service for tracing. Defaults to logger name.
            jaeger_endpoint (str): Jaeger collector endpoint. If None, uses console exporter.
        """
        try:
            from .observability.tracing import configure_observability, get_tracer
            from .observability.correlation import correlation_manager
            
            service_name = service_name or self._logger_name
            configure_observability(service_name, jaeger_endpoint)
            self._tracer = get_tracer()
            self.observability_enabled = True
            
            self.logger.info(f"Observability enabled for service: {service_name}")
        except ImportError as e:
            self.logger.warning(f"Observability dependencies not available: {e}")
            self.observability_enabled = False

    def __call__(
        self, 
        dto: TypeDTO, 
        return_type: Type[TypeDTOResponse] | None = None,
        correlation_id: str = None,
        trace_context: Dict = None
    ) -> TypeDTOResponse | None:
        """
        Main function to execute the framework
        :param dto: The data transfer object to execute
        :param return_type: Expected return type
        :param correlation_id: Optional correlation ID for request tracking
        :param trace_context: Optional trace context for distributed tracing
        :return: Any response
        """
        if not self.was_initialized:
            self.build_root_bus()

        if self.bus is None:
            raise SincproFrameworkNotBuilt(
                "Check the decorators are rigistering the features and app services, check the imports of each "
                "feature and app service"
            )

        # If observability is enabled, execute with tracing
        if self.observability_enabled and self._tracer:
            return self._execute_with_observability(dto, return_type, correlation_id, trace_context)
        else:
            # Execute with middleware pipeline (original behavior)
            def executor(processed_dto, **exec_kwargs) -> TypeDTOResponse | None:
                res: TypeDTOResponse | None = self.bus.execute(processed_dto)
                if res is None:
                    return None
                return res

            return self.middleware_pipeline.execute(dto, executor, return_type=return_type)

    def _execute_with_observability(
        self, 
        dto: TypeDTO, 
        return_type: Type[TypeDTOResponse] | None = None,
        correlation_id: str = None,
        trace_context: Dict = None
    ) -> TypeDTOResponse | None:
        """Execute with full observability tracking."""
        from .observability.correlation import correlation_manager
        
        # Set up correlation
        if correlation_id:
            correlation_manager.set_correlation_id(correlation_id)
        else:
            correlation_manager.get_or_create_correlation_id()
        
        # Start tracing
        operation_name = f"framework.execute.{type(dto).__name__}"
        span = self._tracer.start_span(operation_name, trace_context)
        
        try:
            # Add span attributes
            self._tracer.set_attributes(span, {
                "framework.version": "2.5.0",
                "dto.type": type(dto).__name__,
                "service.name": self._logger_name,
                "correlation_id": correlation_manager.get_correlation_id()
            })
            
            # Check if this DTO has traceability enabled
            dto_name = type(dto).__name__
            observability_config = self._get_observability_config(dto_name)
            
            if observability_config.get('traceability', False):
                self._tracer.add_event(span, "framework.execution.start", {
                    "dto_name": dto_name,
                    "traceability_enabled": True
                })
            
            # Track execution time
            start_time = time.time()
            
            # Execute with middleware pipeline
            def executor(processed_dto, **exec_kwargs) -> TypeDTOResponse | None:
                return self._execute_bus_with_tracing(processed_dto, span)

            result = self.middleware_pipeline.execute(dto, executor, return_type=return_type)
            
            # Record successful execution
            duration = time.time() - start_time
            self._tracer.set_attributes(span, {
                "execution.duration_ms": duration * 1000,
                "execution.status": "success"
            })
            
            return result
            
        except Exception as e:
            # Record error
            duration = time.time() - start_time
            self._tracer.record_exception(span, e)
            self._tracer.set_attributes(span, {
                "execution.duration_ms": duration * 1000,
                "execution.status": "error",
                "error.type": type(e).__name__,
                "error.message": str(e)
            })
            raise
        finally:
            span.end()

    def _execute_bus_with_tracing(self, dto: TypeDTO, parent_span) -> TypeDTOResponse | None:
        """Execute bus operations with tracing."""
        dto_name = type(dto).__name__
        observability_config = self._get_observability_config(dto_name)
        
        # Create child span if span=True is configured for this DTO
        if observability_config.get('span', False):
            child_span = self._tracer.tracer.start_span(
                f"bus.execute.{dto_name}",
                context=parent_span.get_span_context()
            )
            try:
                self._tracer.set_attributes(child_span, {
                    "bus.operation": "execute",
                    "dto.name": dto_name,
                    "span_enabled": True
                })
                
                result = self.bus.execute(dto)
                return result
            finally:
                child_span.end()
        else:
            # Execute without additional span
            return self.bus.execute(dto)

    def _get_observability_config(self, dto_name: str) -> Dict[str, bool]:
        """Get observability configuration for a DTO."""
        # Check feature bus metadata
        if hasattr(self._sp_container.feature_bus, 'observability_metadata'):
            feature_metadata = getattr(self._sp_container.feature_bus, 'observability_metadata', {})
            if hasattr(feature_metadata, 'kwargs'):
                feature_metadata = feature_metadata.kwargs
            if dto_name in feature_metadata:
                return feature_metadata[dto_name]
        
        # Check app service bus metadata
        if hasattr(self._sp_container.app_service_bus, 'observability_metadata'):
            app_metadata = getattr(self._sp_container.app_service_bus, 'observability_metadata', {})
            if hasattr(app_metadata, 'kwargs'):
                app_metadata = app_metadata.kwargs
            if dto_name in app_metadata:
                return app_metadata[dto_name]
        
        # Default configuration
        return {'traceability': False, 'span': False}

    def build_root_bus(self):
        """Build the root bus with the dependencies provided by the user"""
        self._add_dependencies_provided_by_user()
        self._add_error_handlers_provided_by_user()
        self.was_initialized = True
        self.bus: FrameworkBus = self._sp_container.framework_bus()

        # Set the loggers
        self.bus.log_after_execution = self.log_after_execution
        self.bus.feature_bus.log_after_execution = (
            self.log_after_execution and self.log_features
        )
        self.bus.app_service_bus.log_after_execution = (
            self.log_after_execution and self.log_app_services
        )

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

    @property
    def logger(self) -> LoggerProxy:
        """Get bundle context logger"""
        if not self._is_logger_configured:
            self._logger = create_logger(self._logger_name)
            self._is_logger_configured = True
        return self._logger

    # Auto-Documentation Methods

    def generate_documentation(self, output_path: str = "FRAMEWORK_DOCS.md", **config) -> str:
        """
        Generate comprehensive documentation for this framework instance

        Args:
            output_path: Path where to save the documentation
            **config: Configuration options for generation

        Returns:
            Path where documentation was saved
        """
        if not self.was_initialized:
            self.build_root_bus()

        # Late import to avoid circular dependencies
        from .generate_documentation import generate_framework_documentation

        return generate_framework_documentation(self, output_path, **config)

    def print_framework_summary(self) -> None:
        """Print a quick summary of the framework components to console"""
        if not self.was_initialized:
            self.build_root_bus()

        # Late import to avoid circular dependencies
        from .generate_documentation import print_framework_summary

        print_framework_summary(self)
