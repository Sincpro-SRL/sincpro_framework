"""
Auto-Documentation Service - Main Entry Point

Single API for generating complete MkDocs-ready documentation and AI-optimized JSON schemas.
"""

from typing import Literal

from sincpro_framework import UseFramework
from sincpro_framework.generate_documentation.domain.models import FrameworkDocs


def build_documentation(
    framework_instances: UseFramework | list[UseFramework],
    output_dir: str = "generated_docs",
    format: Literal["markdown", "json", "both"] = "both",
) -> str:
    """
    Build complete documentation ready for use with MkDocs and/or AI-optimized JSON schemas.

    Args:
        framework_instances: Framework instance(s) to document.
        output_dir: Output directory for documentation.
        format: Output format - "markdown" for MkDocs, "json" for AI schemas, "both" for both.

    Returns:
        str: Path to the generated directory.

    Example:
        ```python
        from sincpro_framework.generate_documentation import build_documentation

        # Generate markdown documentation only (default)
        docs_path = build_documentation(framework_instance)

        # Generate JSON schema for AI consumption
        schema_path = build_documentation(framework_instance, format="json")

        # Generate both markdown and JSON
        both_path = build_documentation(framework_instance, format="both")
        ```
    """
    from sincpro_framework.generate_documentation.infrastructure.framework_docs_extractor import (
        doc_extractor,
    )
    from sincpro_framework.generate_documentation.infrastructure.mkdocs_markdown_generator import (
        markdown_generator,
    )
    from sincpro_framework.generate_documentation.infrastructure.sincpro_introspector import (
        component_finder,
    )
    from sincpro_framework.generate_documentation.infrastructure.static_site_generator import (
        site_generator,
    )

    _framework_instances = framework_instances
    if not isinstance(framework_instances, list):
        _framework_instances: list = [framework_instances]

    framework_docs = []
    for framework_instance in _framework_instances:
        introspector_instance = component_finder.introspect(framework_instance)
        doc = doc_extractor.extract_framework_docs(introspector_instance)
        framework_docs.append(doc)

    if format in ["markdown", "both"]:
        # Generate markdown documentation
        consolidated_docs = markdown_generator.generate_complete_documentation(framework_docs)
        markdown_output = site_generator.generate_site(
            consolidated_docs, output_dir=output_dir, build_static=True
        )

        if format == "markdown":
            return markdown_output

    if format in ["json", "both"]:
        # Generate JSON schema for AI consumption
        json_output = generate_json_schema(framework_docs, output_dir)

        if format == "json":
            return json_output

    # Return the main output directory when generating both
    return output_dir


def generate_json_schema(
    framework_docs: list[FrameworkDocs], output_dir: str = "generated_docs"
) -> str:
    """
    Generate AI-optimized JSON schema from framework documentation.

    This function now combines framework context (how to use the Sincpro Framework)
    with repository-specific component analysis to provide complete AI understanding.

    The generated schema includes:
    - Framework context: Usage patterns, examples, and best practices
    - Repository analysis: Specific components found in the codebase
    - AI integration: Enhanced metadata for code generation and semantic search

    Args:
        framework_docs: List of FrameworkDocs instances
        output_dir: Output directory for JSON schema files

    Returns:
        str: Path to the generated JSON schema file(s)
    """
    import json
    import os

    from sincpro_framework.generate_documentation.infrastructure.json_schema_generator import (
        AIOptimizedJSONSchemaGenerator,
    )

    # Create ai_context subdirectory within output_dir
    ai_context_dir = os.path.join(output_dir, "ai_context")
    os.makedirs(ai_context_dir, exist_ok=True)

    if len(framework_docs) == 1:
        # Single framework - generate single schema file
        generator = AIOptimizedJSONSchemaGenerator(framework_docs[0])
        schema_path = os.path.join(
            ai_context_dir, f"{framework_docs[0].framework_name}_schema.json"
        )
        generator.save_to_file(schema_path)

        print(f"✅ AI-optimized JSON schema with framework context generated: {schema_path}")
        return schema_path
    else:
        # Multiple frameworks - generate consolidated schema
        consolidated_schema = {
            "schema_version": "1.0.0",
            "generated_at": framework_docs[0].generated_at,
            "generated_by": "sincpro_framework",
            "type": "multi_framework_schema",
            "frameworks": [],
        }

        schema_files = []
        for doc in framework_docs:
            generator = AIOptimizedJSONSchemaGenerator(doc)
            individual_schema = generator.generate_complete_schema()
            consolidated_schema["frameworks"].append(individual_schema)

            # Also save individual schemas
            individual_path = os.path.join(
                ai_context_dir, f"{doc.framework_name}_schema.json"
            )
            generator.save_to_file(individual_path)
            schema_files.append(individual_path)

        # Save consolidated schema
        consolidated_path = os.path.join(
            ai_context_dir, "consolidated_frameworks_schema.json"
        )
        with open(consolidated_path, "w", encoding="utf-8") as f:
            json.dump(consolidated_schema, f, indent=2, ensure_ascii=False, default=str)

        print(
            f"✅ Consolidated AI-optimized JSON schema with framework context generated: {consolidated_path}"
        )
        print(f"✅ Individual schemas with framework context: {', '.join(schema_files)}")
        return consolidated_path
