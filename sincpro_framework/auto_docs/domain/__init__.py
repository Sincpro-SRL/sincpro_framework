"""
Domain Layer for Auto-Documentation System

This module exposes all the domain interfaces and models
for the auto-documentation system.
"""

# Protocol Interfaces
from .generator import DocumentationGenerator
from .introspector import FrameworkIntrospector

# Models and Data Classes
from .models import (
    ApplicationServiceMetadata,
    ComponentMetadata,
    DependencyMetadata,
    DTOMetadata,
    FeatureMetadata,
    IntrospectionResult,
)
from .service import DocumentationService

__all__ = [
    # Models
    "ComponentMetadata",
    "DTOMetadata",
    "DependencyMetadata",
    "FeatureMetadata",
    "ApplicationServiceMetadata",
    "IntrospectionResult",
    # Protocols
    "FrameworkIntrospector",
    "DocumentationGenerator",
    "DocumentationService",
]
