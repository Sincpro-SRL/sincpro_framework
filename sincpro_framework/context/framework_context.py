"""
Context implementation for Sincpro Framework

Provides automatic metadata propagation and scope management
with instance-based context storage for proper isolation.
"""

from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:
    from .use_bus import UseFramework


class FrameworkContext:
    """
    Framework context manager that provides automatic metadata propagation
    and scope management with instance-based storage.
    """

    def __init__(self, framework_instance: "UseFramework", context: Dict[str, Any]):
        self._is_entered: bool = False
        self.framework = framework_instance

        self.context: Dict[str, Any] = context
        self.parent_context: Dict[str, Any] = framework_instance._get_context().copy()

    def __enter__(self) -> "UseFramework":
        """Enter the context manager and return framework instance with context"""
        if self._is_entered:
            raise RuntimeError("Context manager is already entered")

        self._is_entered = True
        # Merge contexts: parent context first, then new context overrides
        merged_context = {**self.parent_context, **self.context}
        self.framework._set_context(merged_context)
        self.framework.logger.debug(f"with context: {self.context}")
        self.framework._inject_context_to_services_and_features(merged_context)

        return self.framework

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit the context manager and restore previous context"""
        self.framework._set_context(self.parent_context)
        # Inject the restored context to services
        self.framework._inject_context_to_services_and_features(self.parent_context)
        return False
