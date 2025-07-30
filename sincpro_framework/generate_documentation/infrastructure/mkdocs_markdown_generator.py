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
        """Sanitize type names for better readability and professional presentation"""
        if "__main__." in type_name:
            type_name = type_name.replace("__main__.", "")
        
        # Handle common Python type representations
        type_mappings = {
            "<class 'str'>": "str",
            "<class 'int'>": "int",
            "<class 'float'>": "float",
            "<class 'bool'>": "bool",
            "<class 'list'>": "list",
            "<class 'dict'>": "dict",
            "<class 'tuple'>": "tuple",
            "<class 'set'>": "set",
            "<class 'datetime.datetime'>": "datetime",
            "<class 'datetime.date'>": "date",
            "<class 'datetime.time'>": "time",
            "<class 'decimal.Decimal'>": "Decimal",
            "<class 'uuid.UUID'>": "UUID",
            "typing.Optional": "Optional",
            "typing.Union": "Union",
            "typing.List": "List",
            "typing.Dict": "Dict",
            "typing.Any": "Any",
        }
        
        for old_type, new_type in type_mappings.items():
            if old_type in type_name:
                type_name = type_name.replace(old_type, new_type)
        
        # Remove extra quotes and clean up formatting
        type_name = type_name.replace("'", "").replace('"', '')
        
        # Handle generic types like List[str], Dict[str, int]
        if type_name.startswith("typing."):
            type_name = type_name.replace("typing.", "")
        
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
        """Generate an enhanced and attractive Home page with framework summary"""
        lines = [
            f"# 🚀 {self.docs.framework_name}",
            "",
            f"!!! success \"Welcome to {self.docs.framework_name} Documentation\"",
            f"    📚 **Comprehensive Framework Documentation** - Generated on {self.docs.generated_at}",
            f"    ",
            f"    This documentation provides complete information about framework components,",
            f"    including their APIs, usage examples, and implementation details.",
            "",
            "## 🎯 Overview",
            "",
            "Welcome to the auto-generated documentation for this framework! This comprehensive guide ",
            "will help you understand and effectively use all the components and features available.",
            "",
        ]

        if self.docs.summary:
            s = self.docs.summary
            lines.extend([
                "## 📊 Component Summary",
                "",
                "=== \"📋 Data Models\"",
                "",
                f"    **{s.dtos_count} DTOs** available for data validation and serialization.",
                "    ",
                "    Data Transfer Objects provide type-safe data validation, automatic serialization,",
                "    and comprehensive schema generation for API documentation.",
                "",
                "=== \"⚡ Features\"",
                "",
                f"    **{s.features_count} Features** implementing core framework capabilities.",
                "    ",
                "    Features represent the main business logic components that process commands",
                "    and provide the core functionality of your application.",
                "",
                "=== \"🏢 Services\"",
                "",
                f"    **{s.application_services_count} Application Services** handling business operations.",
                "    ",
                "    Application services orchestrate business logic and coordinate between",
                "    different components of your application architecture.",
                "",
                "=== \"🔌 Dependencies\"",
                "",
                f"    **{s.dependencies_count} Dependencies** for dependency injection.",
                "    ",
                "    Dependency components provide clean separation of concerns and",
                "    enable testable, maintainable code architecture.",
                "",
                "=== \"🔄 Middleware\"",
                "",
                f"    **{s.middlewares_count} Middleware** components for request processing.",
                "    ",
                "    Middleware components handle cross-cutting concerns like logging,",
                "    authentication, validation, and request/response processing.",
                "",
                "## 📈 Quick Stats",
                "",
                "| Metric | Count | Description |",
                "|--------|-------|-------------|",
                f"| **Total Components** | `{s.total_components}` | All registered components |",
                f"| **Functions** | `{s.dependency_functions_count + s.middleware_functions_count}` | Functional components |",
                f"| **Classes** | `{s.dependency_classes_count + s.middleware_classes_count + s.features_count + s.application_services_count}` | Class-based components |",
                f"| **Data Models** | `{s.dtos_count}` | Pydantic validation models |",
                "",
            ])

            # Add navigation section with better formatting
            lines.extend([
                "## 🗺️ Navigation Guide",
                "",
                "!!! tip \"Start Here\"",
                "    New to this framework? Start with the DTOs to understand the data models,",
                "    then explore Features to see the main capabilities.",
                "",
                "### 📖 Documentation Sections",
                "",
                "=== \"📋 Data Models\"",
                "",
                "    **[DTOs (Data Transfer Objects)](dtos.md)**",
                "    ",
                "    - Data validation schemas",
                "    - Request/response models", 
                "    - Type definitions and examples",
                "    - JSON schema generation",
                "",
                "=== \"⚡ Core Features\"",
                "",
                "    **[Features](features.md)**",
                "    ",
                "    - Main business logic components",
                "    - Command processing capabilities",
                "    - Feature implementation details",
                "    - Usage examples and patterns",
                "",
                "=== \"🏢 Business Logic\"",
                "",
                "    **[Application Services](application-services.md)**",
                "    ",
                "    - Business operation orchestrators",
                "    - Service layer components",
                "    - Complex workflow management",
                "    - Integration patterns",
                "",
                "=== \"🔌 System Components\"",
                "",
                "    **[Dependencies](dependencies.md)**",
                "    ",
                "    - Dependency injection system",
                "    - Component registration",
                "    - Service resolution",
                "    - Testing and mocking support",
                "",
                "=== \"🔄 Request Processing\"",
                "",
                "    **[Middlewares](middlewares.md)**",
                "    ",
                "    - Request/response processing",
                "    - Cross-cutting concerns",
                "    - Pipeline components",
                "    - Custom middleware development",
                "",
            ])

            # Add getting started section if we have components
            if s.total_components > 0:
                lines.extend([
                    "## 🚀 Getting Started",
                    "",
                    "!!! example \"Quick Start Example\"",
                    "    ```python",
                    f"    from your_framework import {self.docs.framework_name}",
                    "    ",
                    "    # Initialize the framework",
                    f"    framework = {self.docs.framework_name}()",
                    "    ",
                    "    # Use a DTO for type-safe data",
                ])
                
                if s.dtos_count > 0:
                    lines.extend([
                        "    # (See DTOs section for available models)",
                        "    request_data = YourDTO(field='value')",
                        "    ",
                    ])
                
                if s.features_count > 0:
                    lines.extend([
                        "    # Execute a feature",
                        "    # (See Features section for available features)",
                        "    result = framework.execute_feature(request_data)",
                        "    print(result)",
                    ])
                
                lines.extend([
                    "    ```",
                    "",
                    "### 📚 Next Steps",
                    "",
                    "1. **Explore DTOs** - Understand the data models and validation rules",
                    "2. **Review Features** - Learn about available business capabilities", 
                    "3. **Check Services** - See how complex operations are orchestrated",
                    "4. **Understand Dependencies** - Learn the injection and resolution patterns",
                    "5. **Configure Middleware** - Set up request processing pipelines",
                    "",
                ])

        lines.extend([
            "---",
            "",
            "!!! info \"Documentation Information\"",
            "    - **Auto-generated** by Sincpro Framework",
            f"    - **Generated on:** {self.docs.generated_at}",
            f"    - **Framework:** {self.docs.framework_name}",
            "    - **Format:** MkDocs with Material theme",
            "",
        ])

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
        """Generate enhanced Features documentation"""
        lines = [
            "# ⚡ Features",
            "",
            "!!! info \"Framework Features Overview\"",
            "    Features are the core business logic components that process commands and provide",
            "    the main functionality of your application. Each feature implements a specific capability.",
            "",
            "## 📋 Overview",
            "",
            f"This framework includes **{len(self.docs.features)}** feature components that provide",
            "core functionality and capabilities.",
            "",
            "### ✨ What are Features?",
            "",
            "=== \"🎯 Purpose\"",
            "",
            "    Features represent discrete business capabilities that:",
            "    ",
            "    - Process specific commands or requests",
            "    - Implement business logic and rules",
            "    - Return structured responses",
            "    - Maintain single responsibility principle",
            "",
            "=== \"🔄 Execution Pattern\"",
            "",
            "    ```python",
            "    # Standard feature execution pattern",
            "    feature = SomeFeature()",
            "    command = SomeCommand(param='value')",
            "    result = feature.execute(command)",
            "    ```",
            "",
            "=== \"🏗️ Architecture\"",
            "",
            "    - **Command-Response Pattern** - Each feature processes a command and returns a response",
            "    - **Type Safety** - Strong typing with Pydantic models for validation",
            "    - **Testability** - Easy to unit test and mock",
            "    - **Composability** - Features can be combined and orchestrated",
            "",
        ]

        if self.docs.features:
            lines.extend([
                "---",
                "",
                "## 🎯 Available Features",
                "",
                f"The following **{len(self.docs.features)}** features are available in this framework:",
                "",
            ])

            for feature in self.docs.features:
                lines.extend(self._generate_class_docs(feature))
        else:
            lines.extend([
                "!!! warning \"No Features Available\"",
                "    No features are currently registered in this framework.",
                "    ",
                "    Features are the core business logic components. Consider adding features",
                "    to implement your application's main capabilities.",
                "",
            ])

        # Add usage guide
        lines.extend([
            "---",
            "",
            "## 📚 Usage Guide",
            "",
            "### Implementing a Feature",
            "",
            "```python",
            "from sincpro_framework import Feature",
            "from your_dtos import YourCommand, YourResponse",
            "",
            "class YourFeature(Feature):",
            "    \"\"\"Your feature description\"\"\"",
            "    ",
            "    def execute(self, dto: YourCommand) -> YourResponse:",
            "        # Implement your business logic here",
            "        result = self.process_command(dto)",
            "        return YourResponse(success=True, data=result)",
            "        ",
            "    def process_command(self, dto: YourCommand):",
            "        # Your processing logic",
            "        pass",
            "```",
            "",
            "### Using Features in Your Application",
            "",
            "```python",
            "# Direct usage",
            "feature = YourFeature()",
            "command = YourCommand(param='value')",
            "response = feature.execute(command)",
            "print(response.data)",
            "",
            "# Through framework",
            "framework = UseFramework('my_app')",
            "response = framework(command)  # Auto-routes to appropriate feature",
            "```",
            "",
            "### Testing Features",
            "",
            "```python",
            "import pytest",
            "",
            "def test_your_feature():",
            "    feature = YourFeature()",
            "    command = YourCommand(param='test_value')",
            "    ",
            "    response = feature.execute(command)",
            "    ",
            "    assert response.success is True",
            "    assert response.data == expected_result",
            "```",
            "",
        ])

        return "\n".join(lines)

    def _generate_dtos_page(self) -> str:
        """Generate enhanced DTOs documentation"""
        lines = [
            "# 📋 DTOs (Data Transfer Objects)",
            "",
            "!!! info \"Data Models Overview\"",
            "    Data Transfer Objects provide type-safe data validation, serialization, and schema generation.",
            "    All DTOs are built using Pydantic for automatic validation and documentation.",
            "",
            "## 📋 Overview",
            "",
            f"This framework includes **{len(self.docs.dtos)}** Pydantic models that provide:",
            "",
            "### ✨ Key Features",
            "",
            "=== \"🔒 Validation\"",
            "",
            "    - **Automatic Data Validation** - Input data is validated automatically against schemas",
            "    - **Type Checking** - Runtime type validation with descriptive error messages",
            "    - **Custom Validators** - Support for custom validation logic and constraints",
            "",
            "=== \"🔄 Serialization\"",
            "",
            "    - **JSON Serialization** - Easy conversion to/from JSON format",
            "    - **Dictionary Support** - Convert to/from Python dictionaries",
            "    - **Custom Serializers** - Support for complex data type serialization",
            "",
            "=== \"📖 Documentation\"",
            "",
            "    - **JSON Schema** - Auto-generated schemas for API documentation",
            "    - **Type Hints** - Full type hints and IDE support",
            "    - **Field Descriptions** - Comprehensive field documentation",
            "",
            "=== \"🔧 Development\"",
            "",
            "    - **IDE Support** - Full autocomplete and type checking",
            "    - **Testing** - Easy to test with predictable validation",
            "    - **Debugging** - Clear error messages and field information",
            "",
        ]

        if self.docs.dtos:
            lines.extend([
                "---",
                "",
                "## 📊 Data Models",
                "",
                f"The following **{len(self.docs.dtos)}** data models are available in this framework:",
                "",
            ])

            for dto in self.docs.dtos:
                lines.extend(self._generate_dto_docs(dto))
        else:
            lines.extend([
                "!!! warning \"No DTOs Available\"",
                "    No DTOs are currently registered in this framework.",
                "    ",
                "    Consider adding Pydantic models to enable type-safe data validation",
                "    and automatic documentation generation.",
                "",
            ])

        # Add general usage guide
        lines.extend([
            "---",
            "",
            "## 📚 General Usage Guide",
            "",
            "### Creating DTO Instances",
            "",
            "```python",
            "# Create from keyword arguments",
            "dto = YourDTO(field1='value1', field2='value2')",
            "",
            "# Create from dictionary",
            "data = {'field1': 'value1', 'field2': 'value2'}",
            "dto = YourDTO.model_validate(data)",
            "",
            "# Create from JSON string",
            "json_data = '{\"field1\": \"value1\", \"field2\": \"value2\"}'",
            "dto = YourDTO.model_validate_json(json_data)",
            "```",
            "",
            "### Working with DTOs",
            "",
            "```python",
            "# Access fields",
            "print(dto.field1)",
            "print(dto.field2)",
            "",
            "# Convert to dictionary",
            "dto_dict = dto.model_dump()",
            "",
            "# Convert to JSON",
            "dto_json = dto.model_dump_json()",
            "",
            "# Get JSON schema",
            "schema = YourDTO.model_json_schema()",
            "```",
            "",
            "### Validation and Error Handling",
            "",
            "```python",
            "from pydantic import ValidationError",
            "",
            "try:",
            "    dto = YourDTO(invalid_field='value')",
            "except ValidationError as e:",
            "    print(f'Validation errors: {e.errors()}')",
            "    for error in e.errors():",
            "        print(f'Field: {error[\"loc\"]}, Error: {error[\"msg\"]}')",
            "```",
            "",
        ])

        return "\n".join(lines)

    def _generate_function_docs(self, func: FunctionMetadata) -> List[str]:
        """Generate enhanced documentation for a function"""
        sanitized_module = self._sanitize_module_name(func.module)
        sanitized_signature = self._sanitize_type_name(func.signature)
        sanitized_return_type = self._sanitize_type_name(func.return_type)

        lines = [
            f"### ⚙️ {func.name}",
            "",
            f"!!! info \"Function Information\"",
            f"    **Module:** `{sanitized_module}`",
            f"    **Returns:** `{sanitized_return_type if sanitized_return_type and sanitized_return_type != 'Any' else 'None'}`",
            "",
        ]

        if func.docstring:
            lines.extend([
                f"!!! note \"Description\"",
                f"    {func.docstring}",
                "",
            ])

        # Enhanced signature display
        lines.extend([
            "#### 📝 Signature",
            "",
            "```python",
            f"def {sanitized_signature}",
            "```",
            "",
        ])

        if func.parameters:
            lines.extend([
                "#### 📋 Parameters",
                "",
                "| Parameter | Type | Required | Description |",
                "|-----------|------|----------|-------------|"
            ])
            
            for param_name, param_info in func.parameters.items():
                param_type = self._sanitize_type_name(param_info.get("type", "Any"))
                required = "✅ Yes" if param_info.get("required", False) else "❌ No"
                description = param_info.get("description", "*No description provided*")
                lines.append(f"| `{param_name}` | `{param_type}` | {required} | {description} |")
            
            lines.extend(["", ""])

        # Add usage example
        lines.extend([
            "??? example \"Usage Example\"",
            "    ```python",
            f"    # Call the {func.name} function",
        ])
        
        if func.parameters:
            param_examples = []
            for param_name, param_info in func.parameters.items():
                param_type = param_info.get("type", "Any")
                if "str" in param_type:
                    param_examples.append(f'{param_name}="example"')
                elif "int" in param_type:
                    param_examples.append(f'{param_name}=123')
                elif "bool" in param_type:
                    param_examples.append(f'{param_name}=True')
                else:
                    param_examples.append(f'{param_name}=value')
            
            if param_examples:
                lines.append(f"    result = {func.name}({', '.join(param_examples)})")
            else:
                lines.append(f"    result = {func.name}()")
        else:
            lines.append(f"    result = {func.name}()")
        
        lines.extend([
            "    print(result)",
            "    ```",
            "",
        ])

        return lines

    def _generate_class_docs(self, cls: ClassMetadata) -> List[str]:
        """Generate enhanced documentation for a class"""
        sanitized_module = self._sanitize_module_name(cls.module)

        lines = [
            f"### 🏗️ {cls.name}",
            "",
            f"!!! info \"Class Information\"",
            f"    **Module:** `{sanitized_module}`",
            f"    **Type:** Class",
            "",
        ]

        if cls.docstring:
            lines.extend([
                f"!!! note \"Description\"",
                f"    {cls.docstring}",
                "",
            ])

        if cls.methods:
            lines.extend([
                "#### 🔧 Methods",
                "",
            ])
            
            for method_name, method in cls.methods.items():
                sanitized_method_signature = self._sanitize_type_name(method.signature)
                
                lines.extend([
                    f"##### `{method_name}`",
                    "",
                    "```python",
                    f"{sanitized_method_signature}",
                    "```",
                    "",
                ])
                
                if method.docstring:
                    lines.extend([
                        f"!!! note \"Method Description\"",
                        f"    {method.docstring}",
                        "",
                    ])
                
                # Add method parameters if available
                if hasattr(method, 'parameters') and method.parameters:
                    lines.extend([
                        "**Parameters:**",
                        "",
                        "| Parameter | Type | Description |",
                        "|-----------|------|-------------|"
                    ])
                    
                    for param_name, param_info in method.parameters.items():
                        param_type = self._sanitize_type_name(param_info.get("type", "Any"))
                        description = param_info.get("description", "*No description provided*")
                        lines.append(f"| `{param_name}` | `{param_type}` | {description} |")
                    
                    lines.extend(["", ""])
                
                lines.append("---")
                lines.append("")
        
        # Add usage example for the class
        lines.extend([
            "??? example \"Usage Example\"",
            "    ```python",
            f"    from your_module import {cls.name}",
            "",
            f"    # Create an instance of {cls.name}",
            f"    instance = {cls.name}()",
            "",
        ])
        
        if cls.methods:
            first_method = next(iter(cls.methods.keys()))
            lines.extend([
                f"    # Call a method",
                f"    result = instance.{first_method}()",
                "    print(result)",
            ])
        
        lines.extend([
            "    ```",
            "",
        ])

        return lines

    def _generate_dto_docs(self, dto: PydanticModelMetadata) -> List[str]:
        """Generate enhanced documentation for a DTO/Pydantic model"""
        sanitized_module = self._sanitize_module_name(dto.module)

        lines = [
            f"### 📊 {dto.name}",
            "",
            f"!!! info \"Module Information\"",
            f"    **Module:** `{sanitized_module}`",
            "",
        ]

        if dto.docstring:
            lines.extend([
                f"!!! note \"Description\"",
                f"    {dto.docstring}",
                "",
            ])

        if dto.fields:
            lines.extend([
                "#### 🔧 Fields",
                "",
                "| Field | Type | Required | Default | Description |",
                "|-------|------|----------|---------|-------------|"
            ])
            
            for field_name, field_info in dto.fields.items():
                field_type = self._sanitize_type_name(field_info.get("type", "Any"))
                required = "✅ Yes" if field_info.get("required", False) else "❌ No"
                
                # Handle default values better
                default_value = field_info.get("default")
                if default_value is None:
                    default_display = "`None`"
                elif str(default_value) == "PydanticUndefined":
                    default_display = "*No default*"
                else:
                    default_display = f"`{default_value}`"
                
                description = field_info.get("description", "*No description provided*")
                
                lines.append(f"| `{field_name}` | `{field_type}` | {required} | {default_display} | {description} |")
            
            lines.extend(["", ""])
            
            # Add usage example
            lines.extend([
                "??? example \"Usage Example\"",
                "    ```python",
                f"    from your_module import {dto.name}",
                "",
                f"    # Create an instance of {dto.name}",
                f"    dto = {dto.name}(",
            ])
            
            # Generate example fields
            example_fields = []
            for field_name, field_info in dto.fields.items():
                field_type = field_info.get("type", "Any")
                if "str" in field_type:
                    example_fields.append(f'        {field_name}="example_value"')
                elif "int" in field_type:
                    example_fields.append(f'        {field_name}=123')
                elif "float" in field_type:
                    example_fields.append(f'        {field_name}=123.45')
                elif "bool" in field_type:
                    example_fields.append(f'        {field_name}=True')
                else:
                    example_fields.append(f'        {field_name}="value"')
            
            lines.extend(example_fields)
            lines.extend([
                "    )",
                "",
                "    # Access fields",
                f"    print(dto.{next(iter(dto.fields.keys()), 'field')})",
                "",
                "    # Convert to dictionary",
                "    dto_dict = dto.model_dump()",
                "",
                "    # Create from dictionary",
                f"    dto_from_dict = {dto.name}.model_validate(dto_dict)",
                "    ```",
                "",
            ])

        return lines


markdown_generator = MkDocsMarkdownGenerator()
