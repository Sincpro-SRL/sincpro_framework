"""
Auto-Documentation System

Sistema de auto-documentación para Sincpro Framework.
"""

# Domain interfaces
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

# Main service
from .service import (
    AutoDocumentationService,
    generate_framework_documentation,
    get_documentation_content,
    get_framework_components,
    print_framework_summary,
)
