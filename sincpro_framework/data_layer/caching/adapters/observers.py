"""The observers the framework ships: on the active span, in the process's metrics, counted,
or nowhere.

Context: the default is both `SpanObserver` and `MetricsObserver` — each outcome is an event on
the span of the use case that asked, so a trace shows a stale or fallback answer where it was
served, and one more on `sincpro.cache.outcomes` / `sincpro.idempotency.outcomes`, so a
dashboard shows the rate of it. With neither backend configured both do nothing. An observer
never raises into the call.
"""

import threading
from collections import Counter

from sincpro_framework.observability.api import process
from sincpro_framework.observability.metrics.domain.instruments import (
    Instrument,
    InstrumentKind,
)
from sincpro_framework.observability.metrics.infrastructure.active import active

CACHE_OUTCOMES = Instrument(
    name="sincpro.cache.outcomes",
    kind=InstrumentKind.COUNTER,
    description="What each cache call answered, by namespace",
    label_keys=("sincpro.namespace", "sincpro.outcome"),
)
IDEMPOTENCY_OUTCOMES = Instrument(
    name="sincpro.idempotency.outcomes",
    kind=InstrumentKind.COUNTER,
    description="What each idempotent run did — ran, replayed, in progress, key reused",
    label_keys=("sincpro.namespace", "sincpro.outcome"),
)


class NoObserver:
    def observed(self, outcome: str, namespace: str) -> None:
        return None


class SpanObserver:
    def observed(self, outcome: str, namespace: str) -> None:
        span = process.current_span()
        if span is None:
            return
        try:
            span.add_event(
                "sincpro.caching", {"outcome": outcome, "namespace": namespace or "-"}
            )
        except Exception:
            return


class CountingObserver:
    """How many times each outcome happened, per namespace — for a metrics exporter to read,
    or a test to assert on."""

    def __init__(self) -> None:
        self.counts: Counter[tuple[str, str]] = Counter()
        self._lock = threading.Lock()

    def observed(self, outcome: str, namespace: str) -> None:
        with self._lock:
            self.counts[(namespace, outcome)] += 1

    def of(self, outcome: str, namespace: str = "") -> int:
        return self.counts[(namespace, outcome)]


class MetricsObserver:
    """One more on `instrument` per outcome, in whichever recorder the process uses."""

    def __init__(self, instrument: Instrument) -> None:
        self.instrument = instrument

    def observed(self, outcome: str, namespace: str) -> None:
        active.emit(
            self.instrument,
            1,
            {"sincpro.namespace": namespace or "-", "sincpro.outcome": outcome},
        )


class Observers:
    """Several observers as one — each told every outcome, none able to stop the others."""

    def __init__(self, *observers: object) -> None:
        self.observers = observers

    def observed(self, outcome: str, namespace: str) -> None:
        for observer in self.observers:
            try:
                observer.observed(outcome, namespace)  # type: ignore[attr-defined]
            except Exception:
                continue


def traced_and_measured(instrument: Instrument) -> Observers:
    """The default observer: the span and the metrics."""
    return Observers(SpanObserver(), MetricsObserver(instrument))
