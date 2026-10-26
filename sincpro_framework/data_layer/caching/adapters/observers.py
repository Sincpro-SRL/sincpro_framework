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
from sincpro_framework.observability.metrics import Instrument, InstrumentKind, metrics

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
    """Nothing is observed — for a cache whose outcomes nobody looks at."""

    def observed(self, outcome: str, namespace: str) -> None:
        """Drops the outcome."""
        return None


class SpanObserver:
    """Each outcome as a `sincpro.caching` event on the current span."""

    def observed(self, outcome: str, namespace: str) -> None:
        """An event `{outcome, namespace}` on the active span — nothing outside a span, and a
        span that fails to take it is ignored. An empty namespace is written `-`.

            SpanObserver().observed("stale", "rates")
                ->  event "sincpro.caching" {"outcome": "stale", "namespace": "rates"}
            SpanObserver().observed("hit", "")   ->  {"outcome": "hit", "namespace": "-"}

        The span is the one running now (`process.current_span`), whoever opened it."""
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
        """An empty count, keyed `(namespace, outcome)`."""
        self.counts: Counter[tuple[str, str]] = Counter()
        self._lock = threading.Lock()

    def observed(self, outcome: str, namespace: str) -> None:
        """One more for `(namespace, outcome)`, under a lock — calls come from any thread."""
        with self._lock:
            self.counts[(namespace, outcome)] += 1

    def of(self, outcome: str, namespace: str = "") -> int:
        """How many times `outcome` happened in `namespace` — 0 when it never did.

            observer.of(CacheOutcome.HIT, "catalog")  ->  3

        What a test asserts on.
        """
        return self.counts[(namespace, outcome)]


class MetricsObserver:
    """One more on `instrument` per outcome, in whichever recorder the process uses."""

    def __init__(self, instrument: Instrument) -> None:
        """`instrument` is the counter told — `CACHE_OUTCOMES` or `IDEMPOTENCY_OUTCOMES`."""
        self.instrument = instrument

    def observed(self, outcome: str, namespace: str) -> None:
        """One on the counter, labelled by namespace (`-` when empty) and outcome."""
        metrics.emit(
            self.instrument,
            1,
            {"sincpro.namespace": namespace or "-", "sincpro.outcome": outcome},
        )


class Observers:
    """Several observers as one — each told every outcome, none able to stop the others."""

    def __init__(self, *observers: object) -> None:
        """`observers` are told in the order given."""
        self.observers = observers

    def observed(self, outcome: str, namespace: str) -> None:
        """Tells each observer in turn — one that raises is skipped, the next still told."""
        for observer in self.observers:
            try:
                observer.observed(outcome, namespace)  # type: ignore[attr-defined]
            except Exception:
                continue

    @classmethod
    def traced_and_measured(cls, instrument: Instrument) -> "Observers":
        """The default observer of `Cache` and `Idempotency`: the span and the metrics.

        Observers.traced_and_measured(CACHE_OUTCOMES)
            ->  Observers(SpanObserver(), MetricsObserver(CACHE_OUTCOMES))
        """
        return cls(SpanObserver(), MetricsObserver(instrument))
