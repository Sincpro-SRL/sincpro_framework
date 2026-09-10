"""Shared fixtures for tracing tests.

OTel's TracerProvider can only be configured once globally per process.
The session-scoped fixture sets it up once; the function-scoped one clears
the exporter between tests to keep assertions isolated.
"""

import pytest


@pytest.fixture(scope="session")
def otel_provider():
    """Configure the OTel TracerProvider once for the whole test session."""
    pytest.importorskip("opentelemetry.sdk.trace")

    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return exporter


@pytest.fixture
def otel_setup(otel_provider):
    """Clear the in-memory exporter before each test, then yield it for assertions."""
    otel_provider.clear()
    yield otel_provider
