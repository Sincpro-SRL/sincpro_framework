"""
Markdown Documentation Generator Implementation

Implementación concreta del protocol DocumentationGenerator para formato Markdown.
"""

from ..domain import (
    ApplicationServiceMetadata,
    DTOMetadata,
    FeatureMetadata,
    IntrospectionResult,
)


class MarkdownDocumentationGenerator:
    """
    Implementación concreta del generador de documentación en Markdown
    """

    def __init__(self, **config):
        """
        Inicializa el generador con configuración

        Args:
            **config: Configuración del generador
        """
        self.config = {
            "include_examples": True,
            "include_dependencies": True,
            "include_type_details": True,
            "include_source_links": False,
            **config,
        }

    def generate(self, result: IntrospectionResult) -> str:
        """Genera documentación completa en Markdown"""
        sections = [
            self._generate_header(result),
            self._generate_overview(result),
            self._generate_table_of_contents(),
            self._generate_features_section(result),
            self._generate_app_services_section(result),
            self._generate_dtos_section(result),
            self._generate_dependencies_section(result),
            self._generate_usage_examples(result),
            self._generate_footer(result),
        ]

        return "\n\n".join(filter(None, sections))

    def save_to_file(self, content: str, output_path: str) -> str:
        """Guarda el contenido en un archivo"""
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)
        return output_path

    def _generate_header(self, result: IntrospectionResult) -> str:
        """Genera el encabezado del documento"""
        return f"""# 📚 {result.framework_name.title()} - Auto-Generated Documentation

> **⚠️ IMPORTANT**: This documentation is auto-generated from the framework registry.  
> **Last updated**: {result.introspection_timestamp.strftime('%Y-%m-%d %H:%M:%S')}  
> **Framework version**: {result.version}

---"""

    def _generate_overview(self, result: IntrospectionResult) -> str:
        """Genera la sección de overview"""
        summary = result.summary

        return f"""## 📊 Framework Overview

This framework contains **{summary['total_features']} Features** and **{summary['total_app_services']} Application Services** 
with a total of **{summary['total_dtos']} DTOs** and **{summary['total_dependencies']} global dependencies**.

### Quick Stats

| Component Type | Count | Description |
|---|---|---|
| **Features** | {summary['total_features']} | Atomic business operations (Command/Query handlers) |
| **Application Services** | {summary['total_app_services']} | Complex workflows orchestrating multiple Features |
| **DTOs** | {summary['total_dtos']} | Data Transfer Objects for input/output |
| **Global Dependencies** | {summary['total_dependencies']} | Shared dependencies injected across components |

### Architecture Summary

This framework follows the **Sincpro Framework** architecture patterns:
- **CQRS**: Commands and Queries handled through DTOs
- **Hexagonal Architecture**: Clear separation between business logic and infrastructure
- **Dependency Injection**: IoC container managing all dependencies
- **Registry Pattern**: Dynamic component registration and resolution"""

    def _generate_table_of_contents(self) -> str:
        """Genera tabla de contenidos"""
        return """## 📋 Table of Contents

1. [Framework Overview](#-framework-overview)
2. [Features](#-features)
3. [Application Services](#-application-services)
4. [Data Transfer Objects (DTOs)](#-data-transfer-objects-dtos)
5. [Dependencies](#-dependencies)
6. [Usage Examples](#-usage-examples)

---"""

    def _generate_features_section(self, result: IntrospectionResult) -> str:
        """Genera la sección de Features"""
        if not result.features:
            return "## ⚡ Features\n\nNo features registered in this framework."

        sections = ["## ⚡ Features\n"]
        sections.append(
            "Features are atomic business operations that handle single responsibilities.\n"
        )

        for dto_name, feature in result.features.items():
            sections.append(self._generate_feature_documentation(feature))

        return "\n".join(sections)

    def _generate_app_services_section(self, result: IntrospectionResult) -> str:
        """Genera la sección de Application Services"""
        if not result.app_services:
            return "## 🎯 Application Services\n\nNo application services registered in this framework."

        sections = ["## 🎯 Application Services\n"]
        sections.append(
            "Application Services orchestrate complex workflows involving multiple Features.\n"
        )

        for dto_name, app_service in result.app_services.items():
            sections.append(self._generate_app_service_documentation(app_service))

        return "\n".join(sections)

    def _generate_feature_documentation(self, feature: FeatureMetadata) -> str:
        """Genera documentación para un Feature específico"""

        # Header
        doc = f"### 🔧 {feature.name}\n\n"

        # Basic info
        doc += f"**Class**: `{feature.class_name}`  \n"
        doc += f"**Module**: `{feature.module_path}`  \n"

        if feature.file_path and self.config.get("include_source_links", False):
            doc += f"**Source**: [`{feature.file_path}`]({feature.file_path})\n"

        doc += "\n"

        # Description
        if feature.docstring:
            doc += f"{feature.docstring}\n\n"
        else:
            doc += "*No description available.*\n\n"

        # Input/Output
        doc += "#### Input/Output\n\n"

        if feature.input_dto:
            doc += f"**Input**: `{feature.input_dto.name}`  \n"
            if self.config.get("include_type_details", True):
                doc += self._generate_dto_summary_table(feature.input_dto)
        else:
            doc += "**Input**: *No specific input DTO*\n"

        if feature.output_dto:
            doc += f"**Output**: `{feature.output_dto.name}`  \n"
            if self.config.get("include_type_details", True):
                doc += self._generate_dto_summary_table(feature.output_dto)
        else:
            doc += "**Output**: *Dynamic return type*\n"

        # Dependencies
        if self.config.get("include_dependencies", True) and feature.dependencies:
            doc += "\n#### Dependencies\n\n"
            for dep in feature.dependencies:
                doc += f"- **{dep.name}**: `{dep.type_annotation}`"
                if dep.description:
                    doc += f" - {dep.description}"
                doc += "\n"

        # Usage example
        if self.config.get("include_examples", True):
            doc += self._generate_feature_usage_example(feature)

        doc += "\n---\n"

        return doc

    def _generate_app_service_documentation(
        self, app_service: ApplicationServiceMetadata
    ) -> str:
        """Genera documentación para un ApplicationService específico"""

        # Header
        doc = f"### 🎯 {app_service.name}\n\n"

        # Basic info
        doc += f"**Class**: `{app_service.class_name}`  \n"
        doc += f"**Module**: `{app_service.module_path}`  \n"

        if app_service.file_path and self.config.get("include_source_links", False):
            doc += f"**Source**: [`{app_service.file_path}`]({app_service.file_path})\n"

        doc += "\n"

        # Description
        if app_service.docstring:
            doc += f"{app_service.docstring}\n\n"
        else:
            doc += "*No description available.*\n\n"

        # Input/Output
        doc += "#### Input/Output\n\n"

        if app_service.input_dto:
            doc += f"**Input**: `{app_service.input_dto.name}`  \n"
            if self.config.get("include_type_details", True):
                doc += self._generate_dto_summary_table(app_service.input_dto)
        else:
            doc += "**Input**: *No specific input DTO*\n"

        if app_service.output_dto:
            doc += f"**Output**: `{app_service.output_dto.name}`  \n"
            if self.config.get("include_type_details", True):
                doc += self._generate_dto_summary_table(app_service.output_dto)
        else:
            doc += "**Output**: *Dynamic return type*\n"

        # Dependencies
        if self.config.get("include_dependencies", True) and app_service.dependencies:
            doc += "\n#### Dependencies\n\n"
            for dep in app_service.dependencies:
                doc += f"- **{dep.name}**: `{dep.type_annotation}`"
                if dep.description:
                    doc += f" - {dep.description}"
                doc += "\n"

        # Usage example
        if self.config.get("include_examples", True):
            doc += self._generate_app_service_usage_example(app_service)

        doc += "\n---\n"

        return doc

    def _generate_dtos_section(self, result: IntrospectionResult) -> str:
        """Genera la sección de DTOs"""
        if not result.dtos:
            return (
                "## 📝 Data Transfer Objects (DTOs)\n\nNo DTOs registered in this framework."
            )

        sections = ["## 📝 Data Transfer Objects (DTOs)\n"]
        sections.append("DTOs define the structure of data passed between components.\n")

        for dto_name, dto in result.dtos.items():
            sections.append(self._generate_dto_documentation(dto))

        return "\n".join(sections)

    def _generate_dto_documentation(self, dto: DTOMetadata) -> str:
        """Genera documentación detallada para un DTO"""

        doc = f"### 📄 {dto.name}\n\n"

        doc += f"**Class**: `{dto.class_name}`  \n"
        doc += f"**Module**: `{dto.module_path}`  \n"
        doc += f"**Type**: {'Input' if dto.is_input else 'Output' if dto.is_output else 'General'}\n\n"

        if dto.docstring:
            doc += f"{dto.docstring}\n\n"

        if dto.fields:
            doc += "#### Fields\n\n"
            doc += self._generate_detailed_dto_table(dto)

        if dto.examples and self.config.get("include_examples", True):
            doc += "\n#### Example\n\n```python\n"
            example = dto.examples[0]
            doc += f"{dto.name}(\n"
            for key, value in example.items():
                doc += f"    {key}={repr(value)},\n"
            doc += ")\n```\n"

        doc += "\n---\n"

        return doc

    def _generate_dependencies_section(self, result: IntrospectionResult) -> str:
        """Genera la sección de dependencias globales"""
        if not result.global_dependencies:
            return (
                "## 🔧 Dependencies\n\nNo global dependencies registered in this framework."
            )

        sections = ["## 🔧 Dependencies\n"]
        sections.append("Global dependencies available for injection.\n")

        for dep_name, dep in result.global_dependencies.items():
            sections.append(f"### {dep_name}\n")
            sections.append(f"**Type**: `{dep.type_annotation}`  \n")
            sections.append(f"**Source**: {dep.source}  \n")
            if dep.description:
                sections.append(f"**Description**: {dep.description}\n")
            sections.append("")

        return "\n".join(sections)

    def _generate_usage_examples(self, result: IntrospectionResult) -> str:
        """Genera ejemplos de uso general"""

        example = f"""## 🚀 Usage Examples

### Basic Framework Usage

```python
from sincpro_framework import UseFramework

# Initialize framework
framework = UseFramework("{result.framework_name}")

# Add dependencies if needed
# framework.add_dependency("my_service", MyService())

# Execute a feature or application service
result = framework(YourDTO(...))
print(result)
```

### Generate Documentation

```python
# Generate this documentation
framework.generate_documentation("docs/api.md")

# Print framework summary
framework.print_framework_summary()

# List all registered components
components = framework.list_registered_components()
print(components)
```"""

        return example

    def _generate_footer(self, result: IntrospectionResult) -> str:
        """Genera el pie del documento"""
        return f"""---

*Auto-generated documentation for {result.framework_name}*  
*Generated on: {result.introspection_timestamp.strftime('%Y-%m-%d %H:%M:%S')}*  
*Framework version: {result.version}*"""

    def _generate_dto_summary_table(self, dto: DTOMetadata) -> str:
        """Genera tabla resumen para un DTO"""
        if not dto.fields:
            return "*No fields defined*\n"

        table = "\n| Field | Type | Required |\n|---|---|---|\n"

        for field_name, field_info in dto.fields.items():
            required = "✅" if field_info.get("required", False) else "❌"
            table += f"| `{field_name}` | `{field_info['type']}` | {required} |\n"

        return table + "\n"

    def _generate_detailed_dto_table(self, dto: DTOMetadata) -> str:
        """Genera tabla detallada para un DTO"""
        if not dto.fields:
            return "*No fields defined*\n"

        table = "| Field | Type | Required | Default | Description |\n|---|---|---|---|---|\n"

        for field_name, field_info in dto.fields.items():
            required = "✅" if field_info.get("required", False) else "❌"
            default = field_info.get("default", "N/A")
            description = field_info.get("description", "No description")

            table += f"| `{field_name}` | `{field_info['type']}` | {required} | `{default}` | {description} |\n"

        return table + "\n"

    def _generate_feature_usage_example(self, feature: FeatureMetadata) -> str:
        """Genera ejemplo de uso para un Feature"""

        example = "\n#### Usage Example\n\n```python\n"

        if feature.input_dto and feature.input_dto.examples:
            example_data = feature.input_dto.examples[0]
            example += f"# Create input DTO\n"
            example += f"dto = {feature.input_dto.name}(\n"
            for key, value in example_data.items():
                example += f"    {key}={repr(value)},\n"
            example += ")\n\n"
        else:
            example += f"# Create your input DTO\n"
            example += (
                f"dto = {feature.input_dto.name if feature.input_dto else 'YourDTO'}(...)\n\n"
            )

        example += "# Execute through framework\n"
        example += "result = framework(dto)\n"
        example += "print(result)\n"
        example += "```\n"

        return example

    def _generate_app_service_usage_example(
        self, app_service: ApplicationServiceMetadata
    ) -> str:
        """Genera ejemplo de uso para un ApplicationService"""

        example = "\n#### Usage Example\n\n```python\n"

        if app_service.input_dto and app_service.input_dto.examples:
            example_data = app_service.input_dto.examples[0]
            example += f"# Create input DTO\n"
            example += f"dto = {app_service.input_dto.name}(\n"
            for key, value in example_data.items():
                example += f"    {key}={repr(value)},\n"
            example += ")\n\n"
        else:
            example += f"# Create your input DTO\n"
            example += f"dto = {app_service.input_dto.name if app_service.input_dto else 'YourDTO'}(...)\n\n"

        example += "# Execute through framework\n"
        example += "result = framework(dto)\n"
        example += "print(result)\n"
        example += "```\n"

        return example
