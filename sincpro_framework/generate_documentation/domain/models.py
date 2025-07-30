"""
Data Models for Auto-Documentation System

This module contains all the data classes and metadata structures
used throughout the auto-documentation system.
"""

from typing import Any, Callable, List, Type

from pydantic import BaseModel, Field


class DTOMetadata(BaseModel):
    """Metadata específica para DTOs"""

    classes: List[Type[Any]] = Field(default_factory=list)


class DependencyMetadata(BaseModel):
    """Metadata para dependencias inyectadas"""

    functions: List[Callable[..., Any]] = Field(default_factory=list)
    objects: list[Any] = Field(default_factory=list)


class MiddlewareMetadata(BaseModel):
    """Metadata específica para Middlewares"""

    functions: List[Callable[..., Any]] = Field(default_factory=list)
    objects: List[Any] = Field(default_factory=list)


class FeatureMetadata(BaseModel):
    """Metadata específica para Features"""

    objects: List[Any] = Field(default_factory=list)


class ApplicationServiceMetadata(BaseModel):
    """Metadata específica para Application Services"""

    objects: List[Any] = Field(default_factory=list)


class IntrospectionResult(BaseModel):
    """Resultado completo de la introspección del framework"""

    framework_name: str
    dtos: DTOMetadata
    dependencies: DependencyMetadata
    middlewares: MiddlewareMetadata
    features: FeatureMetadata
    app_services: ApplicationServiceMetadata
