"""
Auto-Documentation Service - Main Entry Point

Punto de entrada principal del sistema de auto-documentación.
"""

from sincpro_framework import UseFramework


def build_documentation(
    framework_instances: UseFramework | list[UseFramework], out_put_dir="generated_docs"
) -> None:
    """
    Construye la documentación del framework dado.

    Args:
        framework_instance: Instancia del framework a documentar.

    Returns:
        Contenido de la documentación generada.
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

    _framework_instances = framework_instances
    if not isinstance(framework_instances, list):
        _framework_instances: list = [framework_instances]

    framework_docs = []
    for framework_instance in _framework_instances:
        introspector_instance = component_finder.introspect(framework_instance)
        doc = doc_extractor.extract_framework_docs(introspector_instance)
        framework_docs.append(doc)

    consolidated_docs = markdown_generator.generate_complete_documentation(framework_docs)
    markdown_generator.write_documentation_files(consolidated_docs, out_put_dir)
