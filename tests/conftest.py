"""Fixtures shared by every test package.

OTel's TracerProvider can only be configured once globally per process: the session-scoped
fixture sets it up once for every package that asserts spans, and the function-scoped one clears
the exporter between tests to keep assertions isolated.
"""

import pytest

from .fixtures import *  # noqa


@pytest.fixture(autouse=True)
def _forget_declared_metric_labels():
    """`UseFramework(metric_labels=...)` declares for the whole process; a test's must not reach
    the next one."""
    yield
    from sincpro_framework.observability.correlation import reset_metric_labels

    reset_metric_labels()


@pytest.fixture(autouse=True)
def _a_process_context_of_its_own():
    """The process level of the context belongs to the interpreter; what a test sets there —
    a default, a store, a shared root — must not reach the next one."""
    from sincpro_framework.context.domain.node import Values
    from sincpro_framework.context.infrastructure.tree import ROOT

    before, above = ROOT.values, ROOT.parent
    ROOT.values = Values(before.current)
    yield
    ROOT.values, ROOT.parent = before, above


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
