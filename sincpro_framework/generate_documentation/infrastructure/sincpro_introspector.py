"""
Framework Introspector Implementation

Implementación concreta del protocol FrameworkIntrospector para Sincpro Framework.
"""

import inspect
from datetime import datetime
from typing import Any, Dict, List, Optional, Type, get_type_hints

from ..domain import (
    ApplicationServiceMetadata,
    DependencyMetadata,
    DTOMetadata,
    FeatureMetadata,
    IntrospectionResult,
)


class SincproFrameworkIntrospector:
    """
    Implementación concreta del introspector para Sincpro Framework
    """

    def __init__(self):
        self.framework = None

    def introspect(self, framework_instance) -> IntrospectionResult:
        """Realiza introspección completa del framework"""
        self.framework = framework_instance

        if not self._is_framework_built():
            raise ValueError("Framework must be built before introspection")

        # Extraer componentes
        features = self._extract_features()
        app_services = self._extract_app_services()
        dtos = self._extract_dtos(features, app_services)
        global_deps = self._extract_global_dependencies()

        return IntrospectionResult(
            framework_name=getattr(framework_instance, "_logger_name", "Unknown"),
            version=self._get_framework_version(),
            introspection_timestamp=datetime.now(),
            features=features,
            app_services=app_services,
            dtos=dtos,
            global_dependencies=global_deps,
        )

    def _is_framework_built(self) -> bool:
        """Verifica si el framework ya fue construido"""
        return hasattr(self.framework, "bus") and self.framework.bus is not None

    def _extract_features(self) -> Dict[str, FeatureMetadata]:
        """Extrae metadata de todos los Features registrados"""
        features = {}
        feature_registry = self._get_feature_registry()

        for dto_name, feature_instance in feature_registry.items():
            feature_class = type(feature_instance)

            # Extraer DTOs de input/output
            input_dto = self._extract_dto_from_registry_key(dto_name)
            output_dto = self._extract_output_dto_from_feature(feature_instance)

            # Crear metadata del feature
            features[dto_name] = FeatureMetadata(
                name=dto_name,
                class_name=feature_class.__name__,
                module_path=feature_class.__module__,
                file_path=self._get_source_file(feature_class),
                line_number=self._get_source_line(feature_class),
                docstring=self._extract_docstring(feature_class),
                input_dto=input_dto,
                output_dto=output_dto,
                dependencies=self._extract_feature_dependencies(feature_instance),
            )

        return features

    def _extract_app_services(self) -> Dict[str, ApplicationServiceMetadata]:
        """Extrae metadata de todos los Application Services registrados"""
        app_services = {}
        app_service_registry = self._get_app_service_registry()

        for dto_name, app_service_instance in app_service_registry.items():
            app_service_class = type(app_service_instance)

            # Extraer DTOs de input/output
            input_dto = self._extract_dto_from_registry_key(dto_name)
            output_dto = self._extract_output_dto_from_app_service(app_service_instance)

            # Crear metadata del app service
            app_services[dto_name] = ApplicationServiceMetadata(
                name=dto_name,
                class_name=app_service_class.__name__,
                module_path=app_service_class.__module__,
                file_path=self._get_source_file(app_service_class),
                line_number=self._get_source_line(app_service_class),
                docstring=self._extract_docstring(app_service_class),
                input_dto=input_dto,
                output_dto=output_dto,
                dependencies=self._extract_app_service_dependencies(app_service_instance),
            )

        return app_services

    def _extract_dtos(self, features: Dict, app_services: Dict) -> Dict[str, DTOMetadata]:
        """Extrae metadata de todos los DTOs utilizados"""
        dtos = {}

        # Recopilar DTOs únicos de features y app services
        all_components = list(features.values()) + list(app_services.values())

        for component in all_components:
            # DTO de input
            if component.input_dto:
                dto_name = component.input_dto.name
                if dto_name not in dtos:
                    dtos[dto_name] = component.input_dto

            # DTO de output
            if component.output_dto:
                dto_name = component.output_dto.name
                if dto_name not in dtos:
                    dtos[dto_name] = component.output_dto

        return dtos

    def _extract_global_dependencies(self) -> Dict[str, DependencyMetadata]:
        """Extrae metadata de dependencias globales"""
        dependencies = {}

        # Obtener dependencias dinámicas del framework
        if hasattr(self.framework, "dynamic_dep_registry"):
            for dep_name, dep_value in self.framework.dynamic_dep_registry.items():
                dependencies[dep_name] = DependencyMetadata(
                    name=dep_name,
                    type_annotation=type(dep_value).__name__,
                    description=f"Dynamic dependency: {dep_name}",
                    source="dynamic",
                )

        return dependencies

    def _extract_dto_from_registry_key(self, dto_name: str) -> Optional[DTOMetadata]:
        """Extrae metadata de un DTO basado en el nombre de registro"""
        try:
            # Buscar la clase DTO en el registry
            feature_registry = self._get_feature_registry()
            app_service_registry = self._get_app_service_registry()

            # Buscar en features
            if dto_name in feature_registry:
                feature_instance = feature_registry[dto_name]
                dto_class = self._get_dto_class_from_feature(feature_instance)
                if dto_class:
                    return self._create_dto_metadata(dto_class, dto_name, is_input=True)

            # Buscar en app services
            if dto_name in app_service_registry:
                app_service_instance = app_service_registry[dto_name]
                dto_class = self._get_dto_class_from_app_service(app_service_instance)
                if dto_class:
                    return self._create_dto_metadata(dto_class, dto_name, is_input=True)

            return None
        except Exception:
            return None

    def _create_dto_metadata(
        self, dto_class: Type, name: str, is_input: bool = True, is_output: bool = False
    ) -> DTOMetadata:
        """Crea metadata para un DTO"""
        fields = self._extract_dto_fields(dto_class)

        return DTOMetadata(
            name=name,
            class_name=dto_class.__name__ if dto_class else name,
            module_path=dto_class.__module__ if dto_class else "unknown",
            file_path=self._get_source_file(dto_class) if dto_class else None,
            line_number=self._get_source_line(dto_class) if dto_class else None,
            docstring=self._extract_docstring(dto_class) if dto_class else None,
            fields=fields,
            is_input=is_input,
            is_output=is_output,
            examples=self._generate_dto_examples(fields) if fields else [],
        )

    def _extract_dto_fields(self, dto_class: Type) -> Dict[str, Any]:
        """Extrae los campos de un DTO"""
        if not dto_class:
            return {}

        try:
            # Usar type hints para obtener los campos
            type_hints = get_type_hints(dto_class)
            fields = {}

            for field_name, field_type in type_hints.items():
                if field_name.startswith("_"):
                    continue

                fields[field_name] = {
                    "type": str(field_type),
                    "required": not self._is_optional_type(field_type),
                    "default": getattr(dto_class, field_name, None),
                    "description": f"Field {field_name} of type {field_type}",
                }

            return fields
        except Exception:
            return {}

    def _generate_dto_examples(self, fields: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Genera ejemplos de uso para un DTO"""
        if not fields:
            return []

        example = {}
        for field_name, field_info in fields.items():
            example[field_name] = self._generate_example_value(field_info["type"])

        return [example]

    def _generate_example_value(self, type_str: str) -> Any:
        """Genera un valor de ejemplo para un tipo"""
        if "str" in type_str.lower():
            return "example_string"
        elif "int" in type_str.lower():
            return 42
        elif "float" in type_str.lower():
            return 3.14
        elif "bool" in type_str.lower():
            return True
        elif "list" in type_str.lower():
            return ["example_item"]
        elif "dict" in type_str.lower():
            return {"key": "value"}
        else:
            return "example_value"

    def _get_feature_registry(self) -> Dict[str, Any]:
        """Obtiene el registry de Features del framework"""
        try:
            return self.framework.bus.feature_bus.feature_registry
        except AttributeError:
            return {}

    def _get_app_service_registry(self) -> Dict[str, Any]:
        """Obtiene el registry de ApplicationServices del framework"""
        try:
            return self.framework.bus.app_service_bus.app_service_registry
        except AttributeError:
            return {}

    def _get_framework_version(self) -> str:
        """Obtiene la versión del framework"""
        try:
            import sincpro_framework

            return getattr(sincpro_framework, "__version__", "unknown")
        except:
            return "unknown"

    def _get_source_file(self, cls: Type) -> Optional[str]:
        """Obtiene el archivo fuente de una clase"""
        try:
            return inspect.getfile(cls)
        except:
            return None

    def _get_source_line(self, cls: Type) -> Optional[int]:
        """Obtiene la línea de definición de una clase"""
        try:
            return inspect.getsourcelines(cls)[1]
        except:
            return None

    def _extract_docstring(self, cls: Type) -> Optional[str]:
        """Extrae y limpia el docstring de una clase"""
        if cls and cls.__doc__:
            return inspect.cleandoc(cls.__doc__)
        return None

    def _is_optional_type(self, type_hint) -> bool:
        """Verifica si un type hint es Optional"""
        type_str = str(type_hint)
        return "Optional" in type_str or "Union" in type_str

    def _get_dto_class_from_feature(self, feature_instance) -> Optional[Type]:
        """Obtiene la clase DTO asociada a un Feature"""
        # Implementación simplificada
        return None

    def _get_dto_class_from_app_service(self, app_service_instance) -> Optional[Type]:
        """Obtiene la clase DTO asociada a un ApplicationService"""
        # Implementación simplificada
        return None

    def _extract_output_dto_from_feature(self, feature_instance) -> Optional[DTOMetadata]:
        """Extrae el DTO de salida de un Feature"""
        # Implementación simplificada
        return None

    def _extract_output_dto_from_app_service(
        self, app_service_instance
    ) -> Optional[DTOMetadata]:
        """Extrae el DTO de salida de un ApplicationService"""
        # Implementación simplificada
        return None

    def _extract_feature_dependencies(self, feature_instance) -> List[DependencyMetadata]:
        """Extrae las dependencias de un Feature"""
        # Implementación simplificada
        return []

    def _extract_app_service_dependencies(
        self, app_service_instance
    ) -> List[DependencyMetadata]:
        """Extrae las dependencias de un ApplicationService"""
        # Implementación simplificada
        return []
