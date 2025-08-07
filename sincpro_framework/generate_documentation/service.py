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

        # Generate markdown documentation only
        docs_path = build_documentation(framework_instance, format="markdown")

        # Generate chunked JSON schema for AI consumption (default optimized format)
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
        # Generate chunked JSON schema for AI consumption (default optimized format)
        json_output = generate_chunked_json_schema(framework_docs, output_dir)

        if format == "json":
            return json_output

    # Return the main output directory when generating both
    return output_dir


def generate_chunked_json_schema(
    framework_docs: list[FrameworkDocs], output_dir: str = "generated_docs"
) -> str:
    """
    Generate chunked AI-optimized JSON schemas for optimized token consumption.

    This creates multiple smaller JSON files organized for progressive AI discovery:
    - General framework context (reusable across all instances)
    - Framework instance overview (lightweight context per instance)
    - Component-specific chunks (DTOs, Features, etc.)
    - Detail-level chunks for when full information is needed

    The chunked approach allows AIs to:
    1. Start with framework understanding (01_framework_context.json)
    2. Get instance overview (01_<name>_context.json)
    3. Discover available components without full details
    4. Load specific detailed chunks when needed

    Args:
        framework_docs: List of FrameworkDocs instances
        output_dir: Output directory for JSON schema files

    Returns:
        str: Path to the ai_context directory containing all chunked files
    """
    import os

    from sincpro_framework.generate_documentation.infrastructure.json_schema_generator import (
        ChunkedAIJSONSchemaGenerator,
    )

    # Create ai_context subdirectory within output_dir
    ai_context_dir = os.path.join(output_dir, "ai_context")
    os.makedirs(ai_context_dir, exist_ok=True)

    # Generate framework context once (shared across all instances)
    framework_context_path = os.path.join(ai_context_dir, "01_framework_context.json")
    generator = ChunkedAIJSONSchemaGenerator()
    generator.save_framework_context_to_file(framework_context_path)

    generated_files = [framework_context_path]

    # Generate chunked files for each framework instance
    for i, doc in enumerate(framework_docs, 1):
        instance_generator = ChunkedAIJSONSchemaGenerator(doc)
        instance_files = instance_generator.generate_all_chunks(ai_context_dir, i)
        generated_files.extend(instance_files)

    print(f"✅ Generated {len(generated_files)} chunked AI context files:")
    for file_path in generated_files:
        filename = os.path.basename(file_path)
        print(f"   - {filename}")

    return ai_context_dir
