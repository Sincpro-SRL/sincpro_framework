from functools import partial
from typing import Any, Callable, Dict, Mapping, Optional, Type, cast

from sincpro_log.logger import LoggerProxy, create_logger

from . import ioc
from .bus import FrameworkBus
from .context.framework_context import FrameworkContext
from .context.mixin import ContextMixin
from .error_handler import ErrorHandler, build_error_handler_chain
from .exceptions import DependencyAlreadyRegistered, SincproFrameworkNotBuilt
from .middleware import Middleware, MiddlewarePipeline
from .sincpro_abstractions import TypeDTO, TypeDTOResponse


class UseFramework(ContextMixin):
    """Main class to use the framework, this is the main entry point to configure the framework."""

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
        self._sp_container = ioc.FrameworkContainer(logger_bus=self.logger)  # type: ignore[call-arg]
        self._sp_container.logger_bus = self.logger  # type: ignore[assignment]

        # Decorators
        self.feature = partial(ioc.inject_feature_to_bus, self._sp_container)
        self.app_service = partial(ioc.inject_app_service_to_bus, self._sp_container)

        # Registry for dynamic dep injection
        self.dynamic_dep_registry: Dict[str, Any] = dict()

        # Error handlers — ordered pipeline (first registered, first executed)
        self._global_error_handlers: list[ErrorHandler] = []
        self._feature_error_handlers: list[ErrorHandler] = []
        self._app_service_error_handlers: list[ErrorHandler] = []
        self.global_error_handler: Optional[ErrorHandler] = None
        self.feature_error_handler: Optional[ErrorHandler] = None
        self.app_service_error_handler: Optional[ErrorHandler] = None

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

        # Inject current context to services before execution
        current_context = self._get_context()
        if current_context:
            self._inject_context_to_services_and_features(current_context)

        # Execute with middleware pipeline
        def executor(processed_dto, **exec_kwargs) -> TypeDTOResponse | None:
            assert (
                self.bus is not None
            )  # Help mypy understand this is safe after the check above

            return self.bus.execute(processed_dto)

        return self.middleware_pipeline.execute(dto, executor, return_type=return_type)

    def build_root_bus(self):
        """Build the root bus with the dependencies provided by the user"""
        self._add_dependencies_provided_by_user()
        self._add_error_handlers_provided_by_user()
        self.was_initialized = True
        dto_registry = self._sp_container.dto_registry()

        self.bus = self._sp_container.framework_bus()  # type: ignore[assignment]

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

    def context(self, context_to_set: Mapping[str, Any]) -> FrameworkContext:
        """
        Create a context manager with the specified attributes

        Args:
            context_to_set: Dictionary of context attributes to set

        Returns:
            FrameworkContext instance ready to be used with 'with' statement

        Example:
            with app.context({"correlation_id": "123", "user_id": "456"}) as app_with_context:
                result = app_with_context(some_dto)
        """
        return FrameworkContext(cast(Any, self), context_to_set)

    def add_global_error_handler(self, handler: ErrorHandler):
        """Register a global error handler.

        Signature: ``(error) -> Any``.
        If the handler re-raises, the framework calls the next handler in the chain.
        First registered = first to execute.

        Example::

            def base(error):
                return ErrorResponse(str(error))

            app.add_global_error_handler(base)

            def logger(error):
                notify_sentry(error)
                raise error  # delegates to base

            app.add_global_error_handler(logger)
        """
        if not callable(handler):
            raise TypeError("The handler must be a callable")
        self._global_error_handlers.append(handler)
        self.global_error_handler = build_error_handler_chain(self._global_error_handlers)
        if self.was_initialized and self.bus is not None:
            self.bus.handle_error = self.global_error_handler

    def add_feature_error_handler(self, handler: ErrorHandler):
        """Register a feature-level error handler.

        Same semantics as ``add_global_error_handler``.
        """
        if not callable(handler):
            raise TypeError("The handler must be a callable")
        self._feature_error_handlers.append(handler)
        self.feature_error_handler = build_error_handler_chain(self._feature_error_handlers)
        if self.was_initialized and self.bus is not None:
            self.bus.feature_bus.handle_error = self.feature_error_handler

    def add_app_service_error_handler(self, handler: ErrorHandler):
        """Register an app service-level error handler.

        Same semantics as ``add_global_error_handler``.
        """
        if not callable(handler):
            raise TypeError("The handler must be a callable")
        self._app_service_error_handlers.append(handler)
        self.app_service_error_handler = build_error_handler_chain(
            self._app_service_error_handlers
        )
        if self.was_initialized and self.bus is not None:
            self.bus.app_service_bus.handle_error = self.app_service_error_handler

    def _add_dependencies_provided_by_user(self):
        if "feature_registry" in self._sp_container.feature_bus.attributes:
            feature_registry = self._sp_container.feature_bus.attributes[
                "feature_registry"
            ].kwargs

            for _, feature in feature_registry.items():
                feature.add_attributes(**self.dynamic_dep_registry)

        if "app_service_registry" in self._sp_container.app_service_bus.attributes:
            app_service_registry = self._sp_container.app_service_bus.attributes[
                "app_service_registry"
            ].kwargs

            for _, app_service in app_service_registry.items():
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
        return self._logger  # type: ignore[return-value]
