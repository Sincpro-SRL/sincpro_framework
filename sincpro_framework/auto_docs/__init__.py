"""
Auto-Documentation System for Sincpro Framework

Sistema de documentación automática basado en introspección del framework,
siguiendo arquitectura de domain/infrastructure con protocols de Python.
"""

from .domain import (
    ApplicationServiceMetadata,
    ComponentMetadata,
    DependencyMetadata,
    DocumentationGenerator,
    DTOMetadata,
    FeatureMetadata,
    FrameworkIntrospector,
    IntrospectionResult,
)
from .infrastructure.service import AutoDocumentationService


# API de conveniencia para uso externo
def generate_framework_documentation(
    framework_instance, output_path: str = "docs/framework_api_reference.md", **config
) -> str:
    """
    Función de conveniencia para generar documentación del framework

    Args:
        framework_instance: Instancia construida del framework
        output_path: Ruta donde guardar la documentación
        **config: Opciones de configuración

    Returns:
        Ruta donde se guardó la documentación
    """
    service = AutoDocumentationService()
    return service.generate_documentation(framework_instance, output_path, **config)


def print_framework_summary(framework_instance) -> None:
    """Imprime un resumen del framework en consola"""
    service = AutoDocumentationService()
    service.print_framework_summary(framework_instance)


def get_framework_components(framework_instance) -> dict:
    """Obtiene lista de componentes registrados en el framework"""
    service = AutoDocumentationService()
    return service.list_registered_components(framework_instance)


def get_documentation_content(
    framework_instance, format_type: str = "markdown", **config
) -> str:
    """Genera contenido de documentación sin guardar en archivo"""
    service = AutoDocumentationService()
    return service.generate_documentation_content(framework_instance, format_type, **config)


__all__ = [
    # Domain interfaces
    "FrameworkIntrospector",
    "DocumentationGenerator",
    "IntrospectionResult",
    "ComponentMetadata",
    "DTOMetadata",
    "FeatureMetadata",
    "ApplicationServiceMetadata",
    "DependencyMetadata",
    # Infrastructure
    "AutoDocumentationService",
    # Public API
    "generate_framework_documentation",
    "print_framework_summary",
    "get_framework_components",
    "get_documentation_content",
]
