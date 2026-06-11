"""
Observability and tracing module for Sincpro Framework.

OTel is fully optional — all imports are lazy. The framework works identically
whether opentelemetry is installed or not.

Public API:
  - FrameworkSpanContext   — returned by UseFramework.with_trace()
  - setup_otlp_provider    — called by UseFramework.build_root_bus()
"""

from .provider import setup_otlp_provider
from .span_context import FrameworkSpanContext

__all__ = ["FrameworkSpanContext", "setup_otlp_provider"]
