"""
Introspector Protocol

Protocol that defines the interface for framework introspection.
"""

from typing import Protocol

from sincpro_framework.generate_documentation.domain.models import IntrospectionResult
from sincpro_framework.use_bus import UseFramework


class FrameworkIntrospector(Protocol):
    """
    Protocol que define la interfaz para introspección de frameworks
    """

    def introspect(self, framework_instance: UseFramework) -> IntrospectionResult:
        """
        Realiza introspección completa del framework

        Args:
            framework_instance: Instancia del framework a inspeccionar

        Returns:
            Resultado completo de la introspección
        """
        ...
