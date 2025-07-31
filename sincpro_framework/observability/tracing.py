"""Tracing functionality for Sincpro Framework using OpenTelemetry."""

import time
from typing import Dict, Any, Optional
from opentelemetry import trace
from opentelemetry.exporter.jaeger.thrift import JaegerExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator


class SincproTracer:
    """Enhanced tracer for Sincpro Framework."""

    def __init__(self, service_name: str, jaeger_endpoint: str = None):
        # Configure resource
        resource = Resource.create(
            {
                "service.name": service_name,
                "service.version": "2.5.0",
                "framework.name": "sincpro-framework",
            }
        )

        # Set up tracer provider
        trace.set_tracer_provider(TracerProvider(resource=resource))
        tracer_provider = trace.get_tracer_provider()

        # Configure exporters
        if jaeger_endpoint:
            jaeger_exporter = JaegerExporter(
                agent_host_name="localhost",
                agent_port=6831,
                collector_endpoint=jaeger_endpoint,
            )
            span_processor = BatchSpanProcessor(jaeger_exporter)
            tracer_provider.add_span_processor(span_processor)
        else:
            # For development, use console exporter
            console_exporter = ConsoleSpanExporter()
            span_processor = BatchSpanProcessor(console_exporter)
            tracer_provider.add_span_processor(span_processor)

        # Get tracer
        self.tracer = trace.get_tracer(__name__)
        self.propagator = TraceContextTextMapPropagator()

    def start_span(
        self, operation_name: str, parent_context: Optional[Dict] = None
    ) -> trace.Span:
        """Start a new span with optional parent context."""
        if parent_context:
            # Extract context from headers/metadata
            context = self.propagator.extract(parent_context)
            return self.tracer.start_span(operation_name, context=context)
        else:
            return self.tracer.start_span(operation_name)

    def inject_context(self, span: trace.Span) -> Dict[str, str]:
        """Inject span context into headers for propagation."""
        headers = {}
        self.propagator.inject(headers, context=trace.set_span_in_context(span))
        return headers

    def add_event(self, span: trace.Span, name: str, attributes: Dict[str, Any] = None):
        """Add event to span."""
        span.add_event(name, attributes or {})

    def set_attributes(self, span: trace.Span, attributes: Dict[str, Any]):
        """Set attributes on span."""
        for key, value in attributes.items():
            span.set_attribute(key, value)

    def record_exception(self, span: trace.Span, exception: Exception):
        """Record exception in span."""
        span.record_exception(exception)
        span.set_status(trace.Status(trace.StatusCode.ERROR, str(exception)))


# Global tracer instance
_tracer: Optional[SincproTracer] = None


def get_tracer() -> Optional[SincproTracer]:
    """Get global tracer instance."""
    return _tracer


def configure_observability(service_name: str, jaeger_endpoint: str = None):
    """Configure global observability settings."""
    global _tracer
    _tracer = SincproTracer(service_name, jaeger_endpoint)
    return _tracer
