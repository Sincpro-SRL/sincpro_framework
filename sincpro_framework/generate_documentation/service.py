"""
Auto-Documentation Service - Main Entry Point

Single API for generating complete MkDocs-ready documentation.
"""

from sincpro_framework import UseFramework


def build_documentation(
    framework_instances: UseFramework | list[UseFramework], output_dir: str = "generated_docs"
) -> str:
    """
    Build complete documentation ready for use with MkDocs.

    Args:
        framework_instances: Framework instance(s) to document.
        output_dir: Output directory for documentation.

    Returns:
        str: Path to the generated documentation directory.

    Example:
        ```python
        from sincpro_framework.generate_documentation import build_documentation

        # Generate documentation
        docs_path = build_documentation(framework_instance)

        # Then simply:
        # cd generated_docs && mkdocs serve
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

    consolidated_docs = markdown_generator.generate_complete_documentation(framework_docs)

    return site_generator.generate_site(consolidated_docs, output_dir)
