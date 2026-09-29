"""The process's recorder: chosen once from the configuration, replaceable by code (PRD_03 §4.5).

    SINCPRO_METRICS_BACKEND=prometheus   # the reference stack: prometheus_client, /metrics
    SINCPRO_METRICS_BACKEND=otel         # the OTel meter: the host's provider, or OTLP
    SINCPRO_METRICS_BACKEND=off
    SINCPRO_METRICS_BACKEND=auto         # the default: OTel when it is configured, else off

Context: metrics belong to the process, as the Prometheus registry and the OTel meter provider
do — every bus of it records into one recorder, told apart by the `sincpro.context` label and
by the context prefix of a declared name. Nothing here raises into a use case: a recorder that
fails is logged once per instrument and skipped.
"""

import threading
from collections.abc import Mapping
from importlib.util import find_spec

from sincpro_framework.observability.metrics.domain.instruments import (
    Instrument,
    InstrumentKind,
)
from sincpro_framework.observability.metrics.domain.recorder import Recorder
from sincpro_framework.sincpro_conf import settings
from sincpro_framework.sincpro_logger import logger

_UNSET = object()


def _installed(module: str) -> bool:
    try:
        return find_spec(module) is not None
    except (ImportError, ValueError):  # blocked, or a module without a spec
        return False


def _otel_is_configured() -> bool:
    if not (_installed("opentelemetry") and _installed("opentelemetry.sdk")):
        return False
    if settings.otlp_endpoint:
        return True
    from sincpro_framework.observability.metrics.adapters.otel import (
        host_meter_provider_is_real,
    )

    return host_meter_provider_is_real()


def from_settings() -> Recorder | None:
    """1. `off`: none.
    2. `prometheus`: `PrometheusRecorder` on the default registry — `[prometheus]`.
    3. `otel`: `OtelRecorder` on the host's meter provider, or one this installs for OTLP.
    Final: `auto` is `otel` when a meter provider or an OTLP endpoint is there, else none —
    Prometheus is never chosen by guessing, because it needs its `/metrics` served."""
    backend = (getattr(settings, "metrics_backend", None) or "auto").lower()
    if backend == "off":
        return None
    if backend == "prometheus":
        from sincpro_framework.observability.metrics.adapters.prometheus import (
            PrometheusRecorder,
        )

        return PrometheusRecorder()
    if backend == "otel" or (backend == "auto" and _otel_is_configured()):
        from sincpro_framework.observability.metrics.adapters.otel import OtelRecorder

        return OtelRecorder.for_process()
    return None


class ActiveRecorder:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._chosen: object = _UNSET
        self._failing: set[str] = set()

    def get(self) -> Recorder | None:
        chosen = self._chosen
        if chosen is _UNSET:
            with self._lock:
                if self._chosen is _UNSET:
                    try:
                        self._chosen = from_settings()
                    except Exception as error:
                        logger.warning(f"metrics: no recorder — {error}")
                        self._chosen = None
                chosen = self._chosen
        return chosen  # type: ignore[return-value]

    def set(self, recorder: Recorder | None) -> object:
        """Replace the recorder; answers the previous choice, to hand back to `restore`."""
        with self._lock:
            previous, self._chosen = self._chosen, recorder
            self._failing.clear()
            return previous

    def restore(self, previous: object) -> None:
        with self._lock:
            self._chosen = previous
            self._failing.clear()

    def emit(self, instrument: Instrument, value: float, labels: Mapping[str, str]) -> None:
        """One measurement, shielded: a counter never goes down, and a recorder that raises is
        logged once per instrument and never reaches the use case."""
        recorder = self.get()
        if recorder is None:
            return
        try:
            if instrument.kind == InstrumentKind.HISTOGRAM:
                recorder.record(instrument, value, labels)
            elif instrument.kind == InstrumentKind.COUNTER and value < 0:
                return
            else:
                recorder.add(instrument, value, labels)
        except Exception as error:
            if instrument.name not in self._failing:
                self._failing.add(instrument.name)
                logger.warning(
                    f"metrics: {type(recorder).__name__} failed on {instrument.name} — {error}; "
                    "the use case is unaffected"
                )


active = ActiveRecorder()
