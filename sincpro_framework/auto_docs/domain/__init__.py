"""
Domain Protocols for Auto-Documentation System

This module defines the core protocols (interfaces) that define
the contracts for the auto-documentation system.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Protocol


@dataclass
class ComponentMetadata:
    """Metadata común para todos los componentes del framework"""

    name: str
    class_name: str
    module_path: str
    file_path: Optional[str] = None
    line_number: Optional[int] = None
    docstring: Optional[str] = None
    created_at: datetime = datetime.now()


@dataclass
class DTOMetadata(ComponentMetadata):
    """Metadata específica para DTOs"""

    fields: Optional[Dict[str, Any]] = None
    validators: Optional[List[str]] = None
    examples: Optional[List[Dict[str, Any]]] = None
    is_input: bool = True
    is_output: bool = False

    def __post_init__(self):
        if self.fields is None:
            self.fields = {}
        if self.validators is None:
            self.validators = []
        if self.examples is None:
            self.examples = []


@dataclass
class DependencyMetadata:
    """Metadata para dependencias inyectadas"""

    name: str
    type_annotation: str
    is_optional: bool = False
    default_value: Any = None
    description: Optional[str] = None
    source: str = "ioc"  # ioc, dynamic, factory


@dataclass
class FeatureMetadata(ComponentMetadata):
    """Metadata específica para Features"""

    input_dto: Optional[DTOMetadata] = None
    output_dto: Optional[DTOMetadata] = None
    dependencies: Optional[List[DependencyMetadata]] = None

    def __post_init__(self):
        if self.dependencies is None:
            self.dependencies = []


@dataclass
class ApplicationServiceMetadata(ComponentMetadata):
    """Metadata específica para Application Services"""

    input_dto: Optional[DTOMetadata] = None
    output_dto: Optional[DTOMetadata] = None
    dependencies: Optional[List[DependencyMetadata]] = None
    orchestrated_features: Optional[List[str]] = None

    def __post_init__(self):
        if self.dependencies is None:
            self.dependencies = []
        if self.orchestrated_features is None:
            self.orchestrated_features = []


@dataclass
class IntrospectionResult:
    """Resultado completo de la introspección del framework"""

    framework_name: str
    version: str
    introspection_timestamp: datetime
    features: Dict[str, FeatureMetadata]
    app_services: Dict[str, ApplicationServiceMetadata]
    dtos: Dict[str, DTOMetadata]
    global_dependencies: Dict[str, DependencyMetadata]

    @property
    def summary(self) -> Dict[str, int]:
        """Resumen estadístico del framework"""
        return {
            "total_features": len(self.features),
            "total_app_services": len(self.app_services),
            "total_dtos": len(self.dtos),
            "total_dependencies": len(self.global_dependencies),
        }


class FrameworkIntrospector(Protocol):
    """
    Protocol que define la interfaz para introspección de frameworks
    """

    def introspect(self, framework_instance) -> IntrospectionResult:
        """
        Realiza introspección completa del framework

        Args:
            framework_instance: Instancia del framework a inspeccionar

        Returns:
            Resultado completo de la introspección
        """
        ...


class DocumentationGenerator(Protocol):
    """
    Protocol que define la interfaz para generadores de documentación
    """

    def generate(self, result: IntrospectionResult) -> str:
        """
        Genera documentación a partir del resultado de introspección

        Args:
            result: Resultado de la introspección del framework

        Returns:
            Contenido de la documentación generada
        """
        ...

    def save_to_file(self, content: str, output_path: str) -> str:
        """
        Guarda el contenido de documentación en un archivo

        Args:
            content: Contenido de la documentación
            output_path: Ruta donde guardar el archivo

        Returns:
            Ruta donde se guardó el archivo
        """
        ...


class DocumentationService(Protocol):
    """
    Protocol que define la interfaz principal del servicio de documentación
    """

    def generate_documentation(self, framework_instance, output_path: str, **config) -> str:
        """
        Genera documentación completa para una instancia de framework

        Args:
            framework_instance: Instancia del framework
            output_path: Ruta donde guardar la documentación
            **config: Configuración para la generación

        Returns:
            Ruta donde se guardó la documentación
        """
        ...

    def get_framework_summary(self, framework_instance) -> Dict[str, Any]:
        """
        Obtiene un resumen del framework

        Args:
            framework_instance: Instancia del framework

        Returns:
            Diccionario con el resumen del framework
        """
        ...
