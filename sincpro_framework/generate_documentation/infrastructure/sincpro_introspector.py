import types

from sincpro_framework.generate_documentation.domain.models import (
    ApplicationServiceMetadata,
    DependencyMetadata,
    DTOMetadata,
    FeatureMetadata,
    IntrospectionResult,
    MiddlewareMetadata,
)
from sincpro_framework.use_bus import FrameworkBus, UseFramework


class SincproComponentFinder:
    """Introspector específico para Sincpro Framework"""

    def introspect(self, framework_instance: UseFramework) -> IntrospectionResult:
        """Realiza introspección completa del framework"""
        self.framework = framework_instance

        if not self.framework.was_initialized:
            raise ValueError("Framework must be built before introspection")

        self.bus: FrameworkBus = self.framework.bus

        return IntrospectionResult(
            framework_name=self.framework._logger_name,
            dtos=self.extract_dtos(),
            dependencies=self.extract_injected_dependencies(),
            middlewares=self.extract_middlewares(),
            features=self.extract_features(),
            app_services=self.extract_app_services(),
        )

    def extract_dtos(self) -> DTOMetadata:
        return DTOMetadata(classes=list(self.bus.dto_registry.values()))

    def extract_features(self) -> FeatureMetadata:
        return FeatureMetadata(objects=list(self.bus.feature_bus.feature_registry.values()))

    def extract_app_services(self) -> ApplicationServiceMetadata:
        return ApplicationServiceMetadata(
            objects=list(self.bus.app_service_bus.app_service_registry.values())
        )

    def extract_injected_dependencies(self) -> DependencyMetadata:
        functions = list()
        instance_objects = list()

        for _, dep in self.framework.dynamic_dep_registry.items():
            if type(dep) in [types.FunctionType, types.LambdaType]:
                functions.append(dep)
            else:
                instance_objects.append(dep)

        return DependencyMetadata(
            functions=functions,
            objects=instance_objects,
        )

    def extract_middlewares(self) -> MiddlewareMetadata:
        functions = list()
        instance_objects = list()

        for middleware in self.framework.middleware_pipeline.middlewares:
            if type(middleware) in [types.FunctionType, types.LambdaType]:
                functions.append(middleware)
            else:
                instance_objects.append(middleware)

        return MiddlewareMetadata(
            functions=functions,
            objects=instance_objects,
        )


component_finder = SincproComponentFinder()
