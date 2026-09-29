"""The observers the framework ships: on the active span, counted, or nowhere.

Context: `SpanObserver` is the default — each outcome is an event on the span of the use case
that asked, so a trace shows a stale or fallback answer where it was served; with OpenTelemetry
not installed, or no span active, it does nothing. An observer never raises into the call.
"""

import threading
from collections import Counter


class NoObserver:
    def observed(self, outcome: str, namespace: str) -> None:
        return None


class SpanObserver:
    def observed(self, outcome: str, namespace: str) -> None:
        try:
            from opentelemetry import trace  # pyright: ignore[reportMissingImports]
        except ImportError:
            return
        try:
            span = trace.get_current_span()
            if span.get_span_context().is_valid:
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
