from functools import partial
from logging import Logger
from typing import Any, Callable, Dict, Optional, Type

from . import ioc
from .bus import FrameworkBus
from .exceptions import DependencyAlreadyRegistered, SincproFrameworkNotBuilt
from .sincpro_abstractions import TypeDTO, TypeDTOResponse
from .sincpro_logger import create_logger


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
        self._logger: Logger | None = None
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

        res: TypeDTOResponse | None = self.bus.execute(dto)
        if res is None:
            return None

        return res

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
    def logger(self) -> Any:
        """Get bundle context logger"""
        if not self._is_logger_configured:
            self._logger = create_logger(self._logger_name)
            self._is_logger_configured = True
        return self._logger
