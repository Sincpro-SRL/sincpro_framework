"""`PrometheusRecorder`: `prometheus_client`, scraped at `/metrics` — Sincpro's reference stack,
`[prometheus]`.

    SINCPRO_METRICS_BACKEND=prometheus                   # or metrics.use(PrometheusRecorder())
    app.mount("/metrics", metrics.recorder.asgi_app())   # FastApiGateway.app() mounts it itself
    metrics.recorder.serve(9464)                         # a worker with no HTTP server of its own

Context: names are translated to Prometheus' own rules — dots to underscores, `_total` on a
counter (added by `prometheus_client`), `_seconds` on a duration — and so are label keys
(`sincpro.context` → `sincpro_context`). Under gunicorn or several uvicorn workers, set
`PROMETHEUS_MULTIPROC_DIR` before the process starts: every worker then writes to that directory
and `/metrics` answers their sum (an up-down counter as `livesum`).
"""

import os
import re
import threading
from collections.abc import Mapping
from typing import Any

from sincpro_framework.observability.metrics.domain.instruments import (
    SECONDS,
    Instrument,
    InstrumentKind,
)
from sincpro_framework.observability.metrics.domain.recorder import Recorder
from sincpro_framework.sincpro_logger import logger

PROMETHEUS_MISSING = "prometheus_client is not installed. Install with: pip install sincpro-framework[prometheus]"

try:
    import prometheus_client  # pyright: ignore[reportMissingImports]
except ImportError as error:  # pragma: no cover - tests/core/test_core_without_extras.py
    raise ImportError(PROMETHEUS_MISSING) from error

MULTIPROCESS_DIR = "PROMETHEUS_MULTIPROC_DIR"
_INVALID = re.compile(r"[^a-zA-Z0-9_:]")


def prometheus_name(instrument: Instrument) -> str:
    """`billing.issue_invoice.pricing` in seconds → `billing_issue_invoice_pricing_seconds`."""
    name = _INVALID.sub("_", instrument.name)
    if instrument.unit == SECONDS and not name.endswith("_seconds"):
        name += "_seconds"
    return name


def prometheus_label(key: str) -> str:
    return _INVALID.sub("_", key)


class PrometheusRecorder(Recorder):
    def __init__(self, registry: Any | None = None) -> None:
        self.registry = registry if registry is not None else prometheus_client.REGISTRY
        self._lock = threading.Lock()
        self._metrics: dict[str, tuple[Instrument, Any]] = {}
        self._differing: set[str] = set()

    def _metric(self, instrument: Instrument) -> tuple[Instrument, Any] | None:
        """The `prometheus_client` metric for `instrument`, created once, with the instrument it
        was created for. An instrument declared again under the same name with another kind is
        refused — Prometheus would raise on it — logged, and its measurements dropped. With
        other labels it is recorded under the labels the metric was created with: a label it
        lacks is dropped, one it has and the measurement does not is empty."""
        name = prometheus_name(instrument)
        known = self._metrics.get(name)
        if known is None:
            with self._lock:
                known = self._metrics.get(name)
                if known is None:
                    known = (instrument, self._created(name, instrument))
                    self._metrics[name] = known
        declared, _ = known
        if declared.kind != instrument.kind:
            logger.warning(
                f"metrics: {instrument.name} is declared twice with different kinds; "
                "the second is not recorded"
            )
            return None
        if declared.label_keys != instrument.label_keys and name not in self._differing:
            self._differing.add(name)
            logger.warning(
                f"metrics: {instrument.name} is recorded with other labels than it was created "
                f"with ({', '.join(declared.label_keys)}); it keeps those"
            )
        return known

    def _created(self, name: str, instrument: Instrument) -> Any:
        labels = [prometheus_label(one) for one in instrument.label_keys]
        common: dict[str, Any] = {
            "name": name,
            "documentation": instrument.description or instrument.name,
            "labelnames": labels,
            "registry": self.registry,
        }
        if instrument.kind == InstrumentKind.COUNTER:
            return prometheus_client.Counter(**common)
        if instrument.kind == InstrumentKind.UP_DOWN:
            if os.environ.get(MULTIPROCESS_DIR):
                common["multiprocess_mode"] = "livesum"
            return prometheus_client.Gauge(**common)
        if instrument.buckets:
            common["buckets"] = instrument.buckets
        return prometheus_client.Histogram(**common)

    def _series(self, known: tuple[Instrument, Any], labels: Mapping[str, str]) -> Any:
        """Every label the metric was created with, empty when the measurement lacks it — an
        empty label is, to Prometheus, no label."""
        declared, metric = known
        if not declared.label_keys:
            return metric
        return metric.labels(
            **{prometheus_label(key): labels.get(key, "") for key in declared.label_keys}
        )

    def add(self, instrument: Instrument, value: float, labels: Mapping[str, str]) -> None:
        known = self._metric(instrument)
        if known is not None:
            self._series(known, labels).inc(value)

    def record(self, instrument: Instrument, value: float, labels: Mapping[str, str]) -> None:
        known = self._metric(instrument)
        if known is not None:
            self._series(known, labels).observe(value)

    # exposition

    def _exposed(self) -> Any:
        """What `/metrics` reads: this registry, or — with several worker processes — the sum
        of what every worker wrote to `PROMETHEUS_MULTIPROC_DIR`."""
        if not os.environ.get(MULTIPROCESS_DIR):
            return self.registry
        from prometheus_client import multiprocess

        collected = prometheus_client.CollectorRegistry()
        multiprocess.MultiProcessCollector(collected)
        return collected

    def exposition(self) -> bytes:
        """The text format a scrape answers."""
        return prometheus_client.generate_latest(self._exposed())

    def asgi_app(self) -> Any:
        """An ASGI app answering the scrape — mount it at `/metrics`."""
        return prometheus_client.make_asgi_app(registry=self._exposed())

    def serve(self, port: int, address: str = "0.0.0.0") -> None:
        """`/metrics` on a port of its own, in a thread — for a worker with no HTTP server."""
        prometheus_client.start_http_server(port, addr=address, registry=self._exposed())
