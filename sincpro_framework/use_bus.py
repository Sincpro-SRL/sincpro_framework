from functools import partial
from typing import Any, Callable, Dict, Optional, Type

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
            res: TypeDTOResponse | None = self.bus.execute(processed_dto)
            if res is None:
                return None
            return res

        return self.middleware_pipeline.execute(dto, executor, return_type=return_type)

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

    def generate_mkdocs_site(self, output_dir: str = "docs/") -> str:
        """
        Generate a complete MkDocs site for this framework

        Args:
            output_dir: Directory where to generate the MkDocs site

        Returns:
            Path to the generated mkdocs.yml file
        """
        if not self.was_initialized:
            self.build_root_bus()

        # Late import to avoid circular dependencies
        from .generate_documentation.infrastructure.mkdocs_generator import MkDocsGenerator

        generator = MkDocsGenerator()
        return generator.generate_mkdocs_site(self, output_dir)

    def serve_docs(self, port: int = 8000, output_dir: str = "docs/"):
        """
        Generate and serve documentation with MkDocs dev server

        Args:
            port: Port to serve on
            output_dir: Directory to generate docs in
        """
        import os
        import subprocess

        mkdocs_path = self.generate_mkdocs_site(output_dir)
        mkdocs_dir = os.path.dirname(mkdocs_path)

        print(f"🚀 Starting documentation server on http://127.0.0.1:{port}")
        print(f"📚 Serving docs from: {mkdocs_dir}")

        try:
            subprocess.run(
                ["mkdocs", "serve", "-f", mkdocs_path, "-a", f"127.0.0.1:{port}"],
                cwd=mkdocs_dir,
            )
        except FileNotFoundError:
            print("❌ MkDocs not found. Install with: pip install mkdocs mkdocs-material")
        except KeyboardInterrupt:
            print("\n📚 Documentation server stopped.")

    def get_registered_dtos(self) -> Dict[str, Any]:
        """
        Get all registered DTO classes for introspection

        Returns:
            Dictionary mapping DTO names to DTO classes

        Example:
            dtos = framework.get_registered_dtos()
            for dto_name, dto_class in dtos.items():
                print(f"DTO: {dto_name}, Class: {dto_class}")
                if hasattr(dto_class, 'model_fields'):
                    print(f"  Pydantic fields: {list(dto_class.model_fields.keys())}")
        """
        if not self.was_initialized:
            self.build_root_bus()

        # Access the DTO registry from the container
        if hasattr(self._sp_container, "dto_registry"):
            return self._sp_container.dto_registry.kwargs
        else:
            return {}

    def get_framework_introspection_data(self) -> Dict[str, Any]:
        """
        Get complete introspection data including DTOs, Features, AppServices, and Middleware

        Returns:
            Complete framework introspection data
        """
        if not self.was_initialized:
            self.build_root_bus()

        introspection_data = {
            "framework_name": self._logger_name,
            "dtos": self.get_registered_dtos(),
            "features": {},
            "app_services": {},
            "middlewares": [],
            "dependencies": list(self.dynamic_dep_registry.keys()),
        }

        # Get feature instances
        if hasattr(self.bus, "feature_bus") and hasattr(
            self.bus.feature_bus, "feature_registry"
        ):
            introspection_data["features"] = self.bus.feature_bus.feature_registry

        # Get app service instances
        if hasattr(self.bus, "app_service_bus") and hasattr(
            self.bus.app_service_bus, "app_service_registry"
        ):
            introspection_data["app_services"] = self.bus.app_service_bus.app_service_registry

        # Get middleware instances
        if hasattr(self, "middleware_pipeline") and hasattr(
            self.middleware_pipeline, "middlewares"
        ):
            introspection_data["middlewares"] = self.middleware_pipeline.middlewares

        return introspection_data
