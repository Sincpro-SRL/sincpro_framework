"""
Data Models for Auto-Documentation System

This module contains all the data classes and metadata structures
used throughout the auto-documentation system.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional


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
