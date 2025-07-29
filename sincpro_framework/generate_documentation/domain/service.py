"""
Documentation Service Protocol

Protocol that defines the main service interface for documentation.
"""

from typing import Any, Dict, Protocol


class DocumentationService(Protocol):
    """
    Protocol que define la interfaz principal del servicio de documentación
    """

    def generate_documentation(self, framework_instance, output_path: str, **config) -> str:
        """
        Genera documentación completa para una instancia de framework

        Args:
            framework_instance: Instancia del framework
            output_path: Ruta donde guardar la documentación
            **config: Configuración para la generación

        Returns:
            Ruta donde se guardó la documentación
        """
        ...

    def get_framework_summary(self, framework_instance) -> Dict[str, Any]:
        """
        Obtiene un resumen del framework

        Args:
            framework_instance: Instancia del framework

        Returns:
            Diccionario con el resumen del framework
        """
        ...
