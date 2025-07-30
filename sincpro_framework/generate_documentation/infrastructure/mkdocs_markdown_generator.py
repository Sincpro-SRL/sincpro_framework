"""
MkDocs Material Markdown Generator

Generates structured markdown content for MkDocs documentation.
Focused solely on content generation, leaving YAML and site generation to specialized components.
"""

from typing import List

from sincpro_framework.generate_documentation.domain.models import (
    ClassMetadata,
    FrameworkDocs,
    FunctionMetadata,
    MkDocsCompleteDocumentation,
    MkDocsFrameworkDocumentation,
    PydanticModelMetadata,
)


class MkDocsMarkdownGenerator:
    """
    Generates markdown content for MkDocs documentation.
    Focused on content generation with clean separation of concerns.
    """

    def generate_complete_documentation(
        self, frameworks: List[FrameworkDocs]
    ) -> MkDocsCompleteDocumentation:
        """
        Generate complete MkDocs documentation model.
        Creates the full documentation structure with markdown content.

        Args:
            frameworks: List of FrameworkDocs to generate documentation for

        Returns:
            MkDocsCompleteDocumentation: Complete documentation model with all pages
        """
        is_multi = len(frameworks) > 1

        if is_multi:
            main_title = "Frameworks Documentation"
            main_index = self._generate_multi_framework_index(frameworks)
        else:
            main_title = frameworks[0].framework_name
            main_index = None

        # Create complete documentation model
        complete_docs = MkDocsCompleteDocumentation(
            is_multi_framework=is_multi, main_title=main_title, main_index_content=main_index
        )

        # Generate documentation for each framework
        for framework in frameworks:
            framework_docs = self._generate_framework_documentation(framework, is_multi)
            complete_docs.frameworks.append(framework_docs)

        return complete_docs

    def write_documentation_files(
        self, documentation: MkDocsCompleteDocumentation, output_dir: str = "generated_docs"
    ) -> str:
        """
        Legacy method for backward compatibility.
        Use StaticSiteGenerator for better control over site generation.
        """
        from sincpro_framework.generate_documentation.infrastructure.static_site_generator import (
            site_generator,
        )

        print(
            "⚠️  Using legacy write_documentation_files. Consider using StaticSiteGenerator directly."
        )
        return site_generator.generate_site(documentation, output_dir)

    def _sanitize_module_name(self, module_name: str) -> str:
        """Sanitize module name for better display"""
        if module_name == "__main__":
            return "Main Script"
        return module_name

    def _sanitize_type_name(self, type_name: str) -> str:
        """Sanitize type names to remove __main__ references"""
        if "__main__." in type_name:
            return type_name.replace("__main__.", "")
        return type_name

    def _generate_framework_documentation(
        self, framework: FrameworkDocs, is_multi: bool
    ) -> MkDocsFrameworkDocumentation:
        """Generate complete documentation for a single framework"""
        self.docs = framework  # Set current framework for private methods

        framework_dir = (
            self._sanitize_framework_name(framework.framework_name) if is_multi else ""
        )

        framework_docs = MkDocsFrameworkDocumentation(
            framework_name=framework.framework_name, framework_dir=framework_dir
        )

        # Add all pages
        if is_multi:
            framework_docs.add_page(
                "index.md", "Overview", self._generate_framework_overview()
            )
        else:
            framework_docs.add_page("index.md", "Home", self._generate_home_page())

        framework_docs.add_page(
            "dependencies.md", "Dependencies", self._generate_dependencies_page()
        )
        framework_docs.add_page(
            "middlewares.md", "Middlewares", self._generate_middlewares_page()
        )
        framework_docs.add_page(
            "application-services.md",
            "Application Services",
            self._generate_application_services_page(),
        )
        framework_docs.add_page("features.md", "Features", self._generate_features_page())
        framework_docs.add_page("dtos.md", "DTOs", self._generate_dtos_page())

        return framework_docs

    def _generate_nav_config(self, documentation: MkDocsCompleteDocumentation) -> str:
        """Generate complete navigation configuration"""
        lines = [f"# MkDocs Navigation Configuration for {documentation.main_title}"]
        lines.append("nav:")

        if documentation.is_multi_framework:
            lines.append("  - Home: index.md")
            for framework in documentation.frameworks:
                lines.append(f"  - {framework.framework_name}:")
                for nav_item in framework.nav_items:
                    file_path = f"{framework.framework_dir}/{nav_item.file_path}"
                    lines.append(f"    - {nav_item.title}: {file_path}")
        else:
            # Single framework
            framework = documentation.frameworks[0]
            for nav_item in framework.nav_items:
                lines.append(f"  - {nav_item.title}: {nav_item.file_path}")

        return "\n".join(lines)

    def _generate_multi_framework_index(self, frameworks: List[FrameworkDocs]) -> str:
        """Generate main index for multiple frameworks"""
        lines = [
            "# Frameworks Documentation",
            "",
            f"Documentation for {len(frameworks)} framework instances.",
            "",
            "## Available Frameworks",
            "",
        ]

        for framework in frameworks:
            framework_dir = self._sanitize_framework_name(framework.framework_name)
            lines.extend(
                [
                    f"### [{framework.framework_name}]({framework_dir}/)",
                    "",
                ]
            )

            if framework.summary:
                s = framework.summary
                lines.extend(
                    [
                        f"- **Total Components:** {s.total_components}",
                        f"- **DTOs:** {s.dtos_count} data models",
                        f"- **Features:** {s.features_count} framework features",
                        f"- **Application Services:** {s.application_services_count} business services",
                        f"- **Dependencies:** {s.dependencies_count} dependency components",
                        f"- **Middlewares:** {s.middlewares_count} middleware components",
                        "",
                    ]
                )

        return "\n".join(lines)

    def _sanitize_framework_name(self, name: str) -> str:
        """Sanitize framework name for directory use"""
        return name.lower().replace(" ", "-").replace("_", "-")

    def _generate_framework_overview(self) -> str:
        """Generate overview page for the current framework"""
        lines = [
            f"# {self.docs.framework_name}",
            "",
            f"Framework documentation generated on {self.docs.generated_at}",
            "",
            "## Framework Overview",
            "",
        ]

        if self.docs.summary:
            s = self.docs.summary
            lines.extend(
                [
                    f"- **Total Components:** {s.total_components}",
                    f"- **DTOs:** {s.dtos_count} data models",
                    f"- **Features:** {s.features_count} framework features",
                    f"- **Application Services:** {s.application_services_count} business services",
                    f"- **Dependencies:** {s.dependencies_count} dependency components",
                    f"- **Middlewares:** {s.middlewares_count} middleware components",
                ]
            )

        return "\n".join(lines)

    def _generate_home_page(self) -> str:
        """Generate Home page with framework summary"""
        lines = [
            f"# {self.docs.framework_name}",
            "",
            f"📚 **Framework Documentation** - Generated on {self.docs.generated_at}",
            "",
            "## 🎯 Overview",
            "",
            "This documentation provides comprehensive information about the framework components,",
            "including their APIs, usage examples, and implementation details.",
            "",
        ]

        if self.docs.summary:
            s = self.docs.summary
            lines.extend(
                [
                    "## 📊 Component Summary",
                    "",
                    "| Component Type | Count | Description |",
                    "|---|---|---|",
                    f"| 📋 **DTOs** | {s.dtos_count} | Data Transfer Objects for validation and serialization |",
                    f"| ⚡ **Features** | {s.features_count} | Core framework features and capabilities |",
                    f"| 🏢 **Application Services** | {s.application_services_count} | Business logic and application layer services |",
                    f"| 🔌 **Dependencies** | {s.dependencies_count} | Dependency injection components |",
                    f"| 🔄 **Middlewares** | {s.middlewares_count} | Request/response processing middleware |",
                    "",
                    f"**Total Components:** {s.total_components}",
                    "",
                ]
            )

            # Add navigation section
            lines.extend(
                [
                    "## 🗺️ Navigation",
                    "",
                    "- [📋 DTOs](dtos.md) - Data models and validation schemas",
                    "- [⚡ Features](features.md) - Framework features and capabilities",
                    "- [🏢 Application Services](application-services.md) - Business logic services",
                    "- [🔌 Dependencies](dependencies.md) - Dependency injection system",
                    "- [🔄 Middlewares](middlewares.md) - Middleware components",
                    "",
                ]
            )

            # Add quick stats if we have components
            if s.total_components > 0:
                lines.extend(
                    [
                        "## 🔍 Quick Stats",
                        "",
                        f"- **Functions:** {s.dependency_functions_count + s.middleware_functions_count} total functions",
                        f"- **Classes:** {s.dependency_classes_count + s.middleware_classes_count + s.features_count + s.application_services_count} total classes",
                        f"- **Data Models:** {s.dtos_count} Pydantic models",
                        "",
                    ]
                )

        return "\n".join(lines)

    def _generate_dependencies_page(self) -> str:
        """Generate Dependencies API documentation"""
        functions = self.docs.get_dependency_functions()
        classes = self.docs.get_dependency_classes()

        lines = [
            "# 🔌 Dependencies",
            "",
            "Dependency injection system components and API.",
            "",
            "## 📋 Overview",
            "",
            f"This framework includes **{len(functions) + len(classes)}** dependency components:",
            "",
            f"- **{len(functions)}** dependency functions",
            f"- **{len(classes)}** dependency classes",
            "",
        ]

        if functions:
            lines.extend(
                [
                    "---",
                    "",
                    "## ⚙️ Dependency Functions",
                    "",
                    "Functions available through the dependency injection system.",
                    "",
                ]
            )
            for func in functions:
                lines.extend(self._generate_function_docs(func))
                lines.append("---")
                lines.append("")

        if classes:
            lines.extend(
                [
                    "## 🏗️ Dependency Classes",
                    "",
                    "Classes available through the dependency injection system.",
                    "",
                ]
            )
            for cls in classes:
                lines.extend(self._generate_class_docs(cls))
                lines.append("---")
                lines.append("")

        return "\n".join(lines)

    def _generate_middlewares_page(self) -> str:
        """Generate Middlewares API documentation"""
        functions = self.docs.get_middleware_functions()
        classes = self.docs.get_middleware_classes()

        lines = [
            "# 🔄 Middlewares",
            "",
            "Middleware system components and API.",
            "",
            "## 📋 Overview",
            "",
            f"This framework includes **{len(functions) + len(classes)}** middleware components",
            "for request/response processing and cross-cutting concerns.",
            "",
            f"- **{len(functions)}** middleware functions",
            f"- **{len(classes)}** middleware classes",
            "",
        ]

        if functions:
            lines.extend(
                [
                    "---",
                    "",
                    "## ⚙️ Middleware Functions",
                    "",
                    "Function-based middlewares for processing requests and responses.",
                    "",
                ]
            )
            for func in functions:
                lines.extend(self._generate_function_docs(func))
                lines.append("---")
                lines.append("")

        if classes:
            lines.extend(
                [
                    "## 🏗️ Middleware Classes",
                    "",
                    "Class-based middlewares with more complex processing logic.",
                    "",
                ]
            )
            for cls in classes:
                lines.extend(self._generate_class_docs(cls))
                lines.append("---")
                lines.append("")

        if not functions and not classes:
            lines.extend(
                [
                    "No middlewares are currently registered in this framework.",
                    "",
                ]
            )

        return "\n".join(lines)

    def _generate_application_services_page(self) -> str:
        """Generate Application Services documentation"""
        lines = [
            "# 🏢 Application Services",
            "",
            "Business logic and application layer services.",
            "",
            "## 📋 Overview",
            "",
            f"This framework includes **{len(self.docs.application_services)}** application services",
            "that handle business logic and orchestrate domain operations.",
            "",
        ]

        if self.docs.application_services:
            lines.extend(
                [
                    "---",
                    "",
                    "## 💼 Available Services",
                    "",
                ]
            )

            for service in self.docs.application_services:
                lines.extend(self._generate_class_docs(service))
                lines.append("---")
                lines.append("")
        else:
            lines.extend(
                [
                    "No application services are currently registered in this framework.",
                    "",
                ]
            )

        return "\n".join(lines)

    def _generate_features_page(self) -> str:
        """Generate Features documentation"""
        lines = [
            "# ⚡ Features",
            "",
            "Framework features and capabilities.",
            "",
            "## 📋 Overview",
            "",
            f"This framework includes **{len(self.docs.features)}** feature components that provide",
            "core functionality and capabilities.",
            "",
        ]

        if self.docs.features:
            lines.extend(
                [
                    "---",
                    "",
                    "## 🎯 Available Features",
                    "",
                ]
            )

            for feature in self.docs.features:
                lines.extend(self._generate_class_docs(feature))
                lines.append("---")
                lines.append("")
        else:
            lines.extend(
                [
                    "No features are currently registered in this framework.",
                    "",
                ]
            )

        return "\n".join(lines)

    def _generate_dtos_page(self) -> str:
        """Generate DTOs documentation"""
        lines = [
            "# 📋 DTOs (Data Transfer Objects)",
            "",
            "Data models including commands and responses for the framework.",
            "",
            "## 📋 Overview",
            "",
            f"This framework includes **{len(self.docs.dtos)}** Pydantic models that provide",
            "data validation, serialization, and type safety.",
            "",
            "### ✨ Features",
            "",
            "- **Automatic Validation** - Input data is validated automatically",
            "- **Type Safety** - Full type hints and IDE support",
            "- **JSON Schema** - Auto-generated schemas for API documentation",
            "- **Serialization** - Easy conversion to/from JSON",
            "",
        ]

        if self.docs.dtos:
            lines.extend(
                [
                    "---",
                    "",
                    "## 📊 Data Models",
                    "",
                ]
            )

            for dto in self.docs.dtos:
                lines.extend(self._generate_dto_docs(dto))
                lines.append("---")
                lines.append("")
        else:
            lines.extend(
                [
                    "No DTOs are currently registered in this framework.",
                    "",
                ]
            )

        return "\n".join(lines)

    def _generate_function_docs(self, func: FunctionMetadata) -> List[str]:
        """Generate documentation for a function"""
        sanitized_module = self._sanitize_module_name(func.module)
        sanitized_signature = self._sanitize_type_name(func.signature)
        sanitized_return_type = self._sanitize_type_name(func.return_type)

        lines = [
            f"### {func.name}",
            "",
            f"**Module:** `{sanitized_module}`",
            "",
            f"```python",
            f"{sanitized_signature}",
            f"```",
            "",
        ]

        if func.docstring:
            lines.extend([func.docstring, ""])

        if func.parameters:
            lines.extend(["**Parameters:**", ""])
            for param_name, param_info in func.parameters.items():
                param_type = self._sanitize_type_name(param_info.get("type", "Any"))
                required = "Required" if param_info.get("required", False) else "Optional"
                lines.append(f"- `{param_name}` ({param_type}) - {required}")
            lines.append("")

        if sanitized_return_type and sanitized_return_type != "Any":
            lines.extend([f"**Returns:** `{sanitized_return_type}`", ""])

        return lines

    def _generate_class_docs(self, cls: ClassMetadata) -> List[str]:
        """Generate documentation for a class"""
        sanitized_module = self._sanitize_module_name(cls.module)

        lines = [
            f"### {cls.name}",
            "",
            f"**Module:** `{sanitized_module}`",
            "",
        ]

        if cls.docstring:
            lines.extend([cls.docstring, ""])

        if cls.methods:
            lines.extend(["**Methods:**", ""])
            for method_name, method in cls.methods.items():
                sanitized_method_signature = self._sanitize_type_name(method.signature)
                lines.extend(
                    [
                        f"#### {method_name}",
                        "",
                        f"```python",
                        f"{sanitized_method_signature}",
                        f"```",
                        "",
                    ]
                )
                if method.docstring:
                    lines.extend([method.docstring, ""])

        return lines

    def _generate_dto_docs(self, dto: PydanticModelMetadata) -> List[str]:
        """Generate documentation for a DTO/Pydantic model"""
        sanitized_module = self._sanitize_module_name(dto.module)

        lines = [
            f"### {dto.name}",
            "",
            f"**Module:** `{sanitized_module}`",
            "",
        ]

        if dto.docstring:
            lines.extend([dto.docstring, ""])

        if dto.fields:
            lines.extend(["**Fields:**", ""])
            for field_name, field_info in dto.fields.items():
                field_type = self._sanitize_type_name(field_info.get("type", "Any"))
                required = "Required" if field_info.get("required", False) else "Optional"
                lines.append(f"- `{field_name}` ({field_type}) - {required}")

                if field_info.get("description"):
                    lines.append(f"  - {field_info['description']}")
                if field_info.get("default") is not None:
                    lines.append(f"  - Default: `{field_info['default']}`")
            lines.append("")

        return lines


markdown_generator = MkDocsMarkdownGenerator()
