"""Observability module for Sincpro Framework.

This module provides tracing, metrics, and correlation functionality
for comprehensive observability of framework operations.
"""

# Import mixin first as it doesn't depend on OpenTelemetry
from .mixin import ObservabilityMixin

# Conditional imports for OpenTelemetry dependencies
try:
    from .tracing import configure_observability, get_tracer
    from .correlation import correlation_manager
    
    __all__ = [
        "configure_observability",
        "get_tracer", 
        "correlation_manager",
        "ObservabilityMixin",
    ]
except ImportError:
    # OpenTelemetry not available, only expose the mixin
    __all__ = [
        "ObservabilityMixin",
    ]
