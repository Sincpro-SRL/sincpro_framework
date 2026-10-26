"""Metrics: every use case measured by itself, more declared with a decorator, a backend chosen
by configuration (PRD_03 §4).

    from sincpro_framework.observability.metrics import field_path, metrics

    @billing.feature(CommandIssueInvoice)
    @metrics.counts(by=field_path(CommandIssueInvoice).currency)
    class IssueInvoice(Feature): ...

Context: the core records nothing and imports no backend; `[prometheus]` or `[opentelemetry]`
bring one, and `OTEL_METRICS_EXPORTER` (or `metrics.use(...)`) picks it. This is the one door
of `field_path`, the field reference the span attributes (`traces`) take too.
"""

from sincpro_framework.observability.field_paths import FieldPath, field_path
from sincpro_framework.observability.metrics.adapters.in_memory import InMemoryRecorder
from sincpro_framework.observability.metrics.domain.instruments import (
    Instrument,
    InstrumentKind,
)
from sincpro_framework.observability.metrics.domain.recorder import Recorder
from sincpro_framework.observability.metrics.entrypoint.facade import Metrics, metrics

__all__ = [
    "FieldPath",
    "InMemoryRecorder",
    "Instrument",
    "InstrumentKind",
    "Metrics",
    "Recorder",
    "metrics",
    "field_path",
]
