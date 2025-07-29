"""
Introspector Protocol

Protocol that defines the interface for framework introspection.
"""

from typing import Protocol

from .models import IntrospectionResult


class FrameworkIntrospector(Protocol):
    """
    Protocol que define la interfaz para introspección de frameworks
    """

    def introspect(self, framework_instance) -> IntrospectionResult:
        """
        Realiza introspección completa del framework

        Args:
            framework_instance: Instancia del framework a inspeccionar

        Returns:
            Resultado completo de la introspección
        """
        ...
