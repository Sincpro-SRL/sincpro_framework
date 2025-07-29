"""
Auto-Documentation Service Implementation

Implementación concreta del servicio principal de auto-documentación.
"""

from typing import Any, Dict

from .markdown_generator import MarkdownDocumentationGenerator
from .sincpro_introspector import SincproFrameworkIntrospector


class AutoDocumentationService:
    """
    Implementación concreta del servicio de auto-documentación
    """

    def __init__(self):
        """Inicializa el servicio con sus dependencias"""
        self.introspector = SincproFrameworkIntrospector()

    def generate_documentation(self, framework_instance, output_path: str, **config) -> str:
        """Genera documentación completa para una instancia de framework"""

        # 1. Introspección del framework
        result = self.introspector.introspect(framework_instance)

        # 2. Generar documentación en Markdown
        generator = MarkdownDocumentationGenerator(**config)
        content = generator.generate(result)

        # 3. Guardar en archivo
        return generator.save_to_file(content, output_path)

    def get_framework_summary(self, framework_instance) -> Dict[str, Any]:
        """Obtiene un resumen del framework"""

        # Introspección del framework
        result = self.introspector.introspect(framework_instance)

        return {
            "framework_name": result.framework_name,
            "version": result.version,
            "timestamp": result.introspection_timestamp,
            "summary": result.summary,
            "components": {
                "features": list(result.features.keys()),
                "app_services": list(result.app_services.keys()),
                "dtos": list(result.dtos.keys()),
                "global_dependencies": list(result.global_dependencies.keys()),
            },
        }

    def generate_documentation_content(
        self, framework_instance, format_type: str = "markdown", **config
    ) -> str:
        """Genera contenido de documentación sin guardar en archivo"""

        # Introspección del framework
        result = self.introspector.introspect(framework_instance)

        # Seleccionar generador según formato
        if format_type.lower() == "markdown":
            generator = MarkdownDocumentationGenerator(**config)
            return generator.generate(result)
        else:
            raise ValueError(f"Formato no soportado: {format_type}")

    def print_framework_summary(self, framework_instance) -> None:
        """Imprime un resumen del framework en consola"""

        # Obtener resumen
        summary_data = self.get_framework_summary(framework_instance)

        print(f"\n🚀 {summary_data['framework_name'].title()} Framework Summary")
        print("=" * 50)
        print(f"📊 Features: {summary_data['summary']['total_features']}")
        print(f"🎯 Application Services: {summary_data['summary']['total_app_services']}")
        print(f"📝 DTOs: {summary_data['summary']['total_dtos']}")
        print(f"🔧 Global Dependencies: {summary_data['summary']['total_dependencies']}")
        print(f"📅 Last Updated: {summary_data['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 50)

        if summary_data["summary"]["total_features"] > 0:
            print("\n⚡ Registered Features:")
            for feature_name in summary_data["components"]["features"]:
                print(f"  • {feature_name}")

        if summary_data["summary"]["total_app_services"] > 0:
            print("\n🎯 Registered Application Services:")
            for app_service_name in summary_data["components"]["app_services"]:
                print(f"  • {app_service_name}")

    def list_registered_components(self, framework_instance) -> Dict[str, Any]:
        """Lista todos los componentes registrados en el framework"""
        summary_data = self.get_framework_summary(framework_instance)
        return summary_data["components"]
