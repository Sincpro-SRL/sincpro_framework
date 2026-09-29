"""`InMemoryRecorder`: every measurement kept in this process — for tests, and for asserting what
a use case measures.

    recorder = InMemoryRecorder()
    with metrics.using(recorder):
        bus(CommandIssueInvoice(...), ResponseIssueInvoice)
    assert recorder.totals("billing.issue_invoice") == {(("currency", "BOB"),): 1}
"""

import threading
from collections.abc import Mapping

from sincpro_framework.observability.metrics.domain.instruments import Instrument
from sincpro_framework.observability.metrics.domain.recorder import Recorder

type LabelSet = tuple[tuple[str, str], ...]


def _label_set(labels: Mapping[str, str]) -> LabelSet:
    return tuple(sorted(labels.items()))


class InMemoryRecorder(Recorder):
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._instruments: dict[str, Instrument] = {}
        self._totals: dict[str, dict[LabelSet, float]] = {}
        self._observed: dict[str, dict[LabelSet, list[float]]] = {}

    def add(self, instrument: Instrument, value: float, labels: Mapping[str, str]) -> None:
        with self._lock:
            self._instruments.setdefault(instrument.name, instrument)
            series = self._totals.setdefault(instrument.name, {})
            key = _label_set(labels)
            series[key] = series.get(key, 0.0) + value

    def record(self, instrument: Instrument, value: float, labels: Mapping[str, str]) -> None:
        with self._lock:
            self._instruments.setdefault(instrument.name, instrument)
            self._observed.setdefault(instrument.name, {}).setdefault(
                _label_set(labels), []
            ).append(value)

    def instrument(self, name: str) -> Instrument:
        """The instrument recorded under `name` — `KeyError` when none was."""
        return self._instruments[name]

    def names(self) -> list[str]:
        return sorted(self._instruments)

    def totals(self, name: str) -> dict[LabelSet, float]:
        """A counter's value per set of labels, each set as sorted `(key, value)` pairs."""
        with self._lock:
            return dict(self._totals.get(name, {}))

    def observations(self, name: str) -> list[tuple[dict[str, str], list[float]]]:
        """A histogram's observations per set of labels, in the order the sets first appeared."""
        with self._lock:
            return [
                (dict(labels), list(values))
                for labels, values in self._observed.get(name, {}).items()
            ]

    def clear(self) -> None:
        with self._lock:
            self._instruments.clear()
            self._totals.clear()
            self._observed.clear()
