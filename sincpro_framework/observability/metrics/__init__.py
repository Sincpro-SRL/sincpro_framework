"""Metrics: every use case measured by itself, more declared with a decorator, a backend chosen
by configuration (PRD_03 §4).

    from sincpro_framework.observability.metrics import metrics, of

    @billing.feature(CommandIssueInvoice)
    @metrics.counts(by=of(CommandIssueInvoice).currency)
    class IssueInvoice(Feature): ...

Context: the core records nothing and imports no backend; `[prometheus]` or `[opentelemetry]`
bring one, and `SINCPRO_METRICS_BACKEND` (or `metrics.use(...)`) picks it.
"""

from sincpro_framework.observability.metrics.adapters.in_memory import InMemoryRecorder
from sincpro_framework.observability.metrics.domain.instruments import (
    Instrument,
    InstrumentKind,
)
from sincpro_framework.observability.metrics.domain.paths import FieldPath, of
from sincpro_framework.observability.metrics.domain.recorder import Recorder
from sincpro_framework.observability.metrics.entrypoint.declare import (
    Metrics,
    declares_metrics,
    metrics,
)

__all__ = [
    "FieldPath",
    "InMemoryRecorder",
    "Instrument",
    "InstrumentKind",
    "Metrics",
    "Recorder",
    "declares_metrics",
    "metrics",
    "of",
]
