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
from sincpro_framework.observability.metrics.domain.declarations import Declaration, Measure
from sincpro_framework.observability.metrics.domain.instruments import (
    SECONDS,
    Instrument,
    InstrumentKind,
)
from sincpro_framework.observability.metrics.domain.naming import metric_name
from sincpro_framework.observability.metrics.domain.paths import FieldPath, of
from sincpro_framework.observability.metrics.domain.recorder import Recorder
from sincpro_framework.observability.metrics.entrypoint.declare import (
    BoundInstrument,
    Metrics,
    declares_metrics,
    metrics,
)
from sincpro_framework.observability.metrics.infrastructure.execution import (
    USE_CASE_DURATION,
    measured,
)

__all__ = [
    "SECONDS",
    "USE_CASE_DURATION",
    "BoundInstrument",
    "Declaration",
    "FieldPath",
    "InMemoryRecorder",
    "Instrument",
    "InstrumentKind",
    "Measure",
    "Metrics",
    "Recorder",
    "declares_metrics",
    "measured",
    "metric_name",
    "metrics",
    "of",
]
