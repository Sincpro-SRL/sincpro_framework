"""
Infrastructure Layer for Auto-Documentation System

Implementaciones concretas de los protocols definidos en el dominio.
"""

from .markdown_generator import MarkdownDocumentationGenerator
from .sincpro_introspector import SincproFrameworkIntrospector

__all__ = [
    "MarkdownDocumentationGenerator",
    "SincproFrameworkIntrospector",
]
