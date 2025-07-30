"""
Data Models for Auto-Documentation System

This module contains all the data classes and metadata structures
used throughout the auto-documentation system.
"""

from typing import Any, Callable, Dict, List, Optional, Type

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


class FunctionMetadata(BaseModel):
    """Metadatos específicos de una función"""

    name: str
    module: str
    docstring: Optional[str] = None
    signature: str
    parameters: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    return_type: str = "Any"
    is_async: bool = False
    is_generator: bool = False
    source_file: Optional[str] = None
    source_line: Optional[int] = None


class ClassMetadata(BaseModel):
    """Metadatos específicos de una clase"""

    name: str
    module: str
    docstring: Optional[str] = None
    bases: List[str] = Field(default_factory=list)
    mro: List[str] = Field(default_factory=list)
    methods: Dict[str, FunctionMetadata] = Field(default_factory=dict)
    attributes: Dict[str, str] = Field(default_factory=dict)
    source_file: Optional[str] = None
    source_line: Optional[int] = None


class PydanticModelMetadata(BaseModel):
    """Metadatos específicos para modelos Pydantic"""

    name: str
    module: str
    docstring: Optional[str] = None
    bases: List[str] = Field(default_factory=list)
    fields: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    model_schema: Optional[Dict[str, Any]] = None
    config: Optional[Dict[str, Any]] = None
    validators: List[str] = Field(default_factory=list)
    source_file: Optional[str] = None
    source_line: Optional[int] = None


class InstanceMetadata(BaseModel):
    """Metadatos específicos de una instancia"""

    class_name: str
    module: str
    object_id: str
    object_repr: str
    class_docstring: Optional[str] = None
    is_pydantic: bool = False
    public_attributes: Dict[str, Dict[str, str]] = Field(default_factory=dict)
    public_methods: Dict[str, Dict[str, str]] = Field(default_factory=dict)
    inheritance: List[str] = Field(default_factory=list)


class FrameworkSummary(BaseModel):
    """Summary information about the framework components"""

    total_components: int
    dtos_count: int
    features_count: int
    application_services_count: int
    middlewares_count: int
    dependencies_count: int

    # Component type breakdowns
    middleware_functions_count: int = 0
    middleware_classes_count: int = 0
    dependency_functions_count: int = 0
    dependency_classes_count: int = 0

    # Additional summary info
    has_dtos: bool = False
    has_features: bool = False
    has_application_services: bool = False
    has_middlewares: bool = False
    has_dependencies: bool = False


class FrameworkDocs(BaseModel):
    """
    Complete framework documentation containing all metadata.
    This is the main model that contains all framework components documentation.
    """

    framework_name: str
    generated_at: str
    generated_by: str = "sincpro_framework"

    # Summary as structured data
    summary: Optional[FrameworkSummary] = None

    # Framework-specific components with proper typing
    dtos: List[PydanticModelMetadata] = Field(default_factory=list)
    features: List[ClassMetadata] = Field(default_factory=list)
    application_services: List[ClassMetadata] = Field(default_factory=list)
    middlewares: List[FunctionMetadata | ClassMetadata] = Field(default_factory=list)
    dependencies: List[FunctionMetadata | ClassMetadata] = Field(default_factory=list)

    def generate_summary(self) -> FrameworkSummary:
        """Generate and set the framework summary"""
        middleware_functions = self.get_middleware_functions()
        middleware_classes = self.get_middleware_classes()
        dependency_functions = self.get_dependency_functions()
        dependency_classes = self.get_dependency_classes()

        total = (
            len(self.dtos)
            + len(self.features)
            + len(self.application_services)
            + len(self.middlewares)
            + len(self.dependencies)
        )

        summary = FrameworkSummary(
            total_components=total,
            dtos_count=len(self.dtos),
            features_count=len(self.features),
            application_services_count=len(self.application_services),
            middlewares_count=len(self.middlewares),
            dependencies_count=len(self.dependencies),
            middleware_functions_count=len(middleware_functions),
            middleware_classes_count=len(middleware_classes),
            dependency_functions_count=len(dependency_functions),
            dependency_classes_count=len(dependency_classes),
            has_dtos=len(self.dtos) > 0,
            has_features=len(self.features) > 0,
            has_application_services=len(self.application_services) > 0,
            has_middlewares=len(self.middlewares) > 0,
            has_dependencies=len(self.dependencies) > 0,
        )

        self.summary = summary
        return summary

    def get_total_components(self) -> Dict[str, int]:
        """Get count of all components (deprecated - use summary instead)"""
        return {
            "dtos": len(self.dtos),
            "features": len(self.features),
            "application_services": len(self.application_services),
            "middlewares": len(self.middlewares),
            "dependencies": len(self.dependencies),
        }

    def get_middleware_functions(self) -> List[FunctionMetadata]:
        """Get only middleware functions"""
        return [m for m in self.middlewares if isinstance(m, FunctionMetadata)]

    def get_middleware_classes(self) -> List[ClassMetadata]:
        """Get only middleware classes"""
        return [m for m in self.middlewares if isinstance(m, ClassMetadata)]

    def get_dependency_functions(self) -> List[FunctionMetadata]:
        """Get only dependency functions"""
        return [d for d in self.dependencies if isinstance(d, FunctionMetadata)]

    def get_dependency_classes(self) -> List[ClassMetadata]:
        """Get only dependency classes"""
        return [d for d in self.dependencies if isinstance(d, ClassMetadata)]

    def get_components_by_module(self, module_name: str) -> Dict[str, List]:
        """Get all components filtered by module"""
        result = {
            "dtos": [dto for dto in self.dtos if dto.module == module_name],
            "features": [f for f in self.features if f.module == module_name],
            "application_services": [
                a for a in self.application_services if a.module == module_name
            ],
            "middlewares": [m for m in self.middlewares if m.module == module_name],
            "dependencies": [d for d in self.dependencies if d.module == module_name],
        }
        return result


class MkDocsPage(BaseModel):
    """Represents a single MkDocs page"""

    filename: str
    title: str
    content: str


class MkDocsNavItem(BaseModel):
    """Represents a navigation item in MkDocs"""

    title: str
    file_path: str


class MkDocsFrameworkDocumentation(BaseModel):
    """Complete MkDocs documentation for a framework"""

    framework_name: str
    framework_dir: str  # sanitized directory name
    pages: List[MkDocsPage] = Field(default_factory=list)
    nav_items: List[MkDocsNavItem] = Field(default_factory=list)

    def add_page(self, filename: str, title: str, content: str):
        """Add a page to the documentation"""
        self.pages.append(MkDocsPage(filename=filename, title=title, content=content))
        self.nav_items.append(MkDocsNavItem(title=title, file_path=filename))


class MkDocsCompleteDocumentation(BaseModel):
    """Complete MkDocs documentation for single or multiple frameworks"""

    is_multi_framework: bool
    main_title: str
    frameworks: List[MkDocsFrameworkDocumentation] = Field(default_factory=list)
    main_index_content: Optional[str] = None
    nav_config: str = ""

    def get_all_files(self) -> Dict[str, str]:
        """Get all files that need to be written (filepath -> content)"""
        files = {}

        # Main index
        if self.main_index_content:
            files["index.md"] = self.main_index_content

        # Framework pages
        for framework in self.frameworks:
            base_path = framework.framework_dir if self.is_multi_framework else ""

            for page in framework.pages:
                if base_path:
                    filepath = f"{base_path}/{page.filename}"
                else:
                    filepath = page.filename
                files[filepath] = page.content

        # Navigation config
        files["mkdocs_nav.yml"] = self.nav_config

        return files
