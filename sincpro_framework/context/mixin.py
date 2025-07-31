from typing import Any, Dict

from sincpro_framework.bus import FrameworkBus


class ContextMixin:
    """
    Mixin to provide context access to Features and ApplicationServices
    """

    bus: FrameworkBus
    _context: Dict[str, Any]

    def _set_context(self, context: Dict[str, Any]) -> None:
        """Set the context for the current framework instance"""
        self._context = context

    def _get_context(self):
        """Get the current context for the framework instance"""
        return self._context if hasattr(self, "_context") else dict()

    def _clean_context(self) -> None:
        """Clean the context for the current framework instance"""
        self._context = dict()

    def _inject_context_to_services_and_features(self, context: Dict[str, Any]) -> None:
        """Update context in all registered services with current context"""

        # Only inject if bus is built and available
        if self.bus is None:
            return

        for feature in self.bus.feature_bus.feature_registry.values():
            feature.context = context.copy()

        for app_service in self.bus.app_service_bus.app_service_registry.values():
            app_service.context = context.copy()
