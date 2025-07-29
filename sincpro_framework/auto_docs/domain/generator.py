"""
Documentation Generator Protocol

Protocol that defines the interface for documentation generation.
"""

from typing import Protocol

from .models import IntrospectionResult


class DocumentationGenerator(Protocol):
    """
    Protocol que define la interfaz para generadores de documentación
    """

    def generate(self, result: IntrospectionResult) -> str:
        """
        Genera documentación a partir del resultado de introspección

        Args:
            result: Resultado de la introspección del framework

        Returns:
            Contenido de la documentación generada
        """
        ...

    def save_to_file(self, content: str, output_path: str) -> str:
        """
        Guarda el contenido de documentación en un archivo

        Args:
            content: Contenido de la documentación
            output_path: Ruta donde guardar el archivo

        Returns:
            Ruta donde se guardó el archivo
        """
        ...
