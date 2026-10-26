"""`InMemoryRecorder`: every measurement held in this process — for tests, and for asserting what
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
    """A set of labels as a hashable key — its pairs sorted, so the order they came in is
    irrelevant.

        _label_set({"currency": "BOB", "channel": "web"})
            ->  (("channel", "web"), ("currency", "BOB"))
    """
    return tuple(sorted(labels.items()))


class InMemoryRecorder(Recorder):
    """Every measurement held in memory, behind one lock — sums per series, observations as
    lists; read back with `totals`, `observations`, `names` and `instrument`."""

    def __init__(self) -> None:
        """Start empty: no instrument seen, no series."""
        self._lock = threading.Lock()
        self._instruments: dict[str, Instrument] = {}
        self._totals: dict[str, dict[LabelSet, float]] = {}
        self._observed: dict[str, dict[LabelSet, list[float]]] = {}

    def add(self, instrument: Instrument, value: float, labels: Mapping[str, str]) -> None:
        """Adds `value` to the series' running total, as given — dropping a counter's negative
        value is the shield's job (`ActiveRecorder.emit`), not this recorder's.

            recorder.add(issued, 1, {"currency": "BOB", "channel": "web"})
            recorder.add(issued, 2, {"channel": "web", "currency": "BOB"})
            recorder.totals("billing.issued")
                ->  {(("channel", "web"), ("currency", "BOB")): 3.0}

        The same labels in any order are one series; the first instrument seen under a name
        is the one retained."""
        with self._lock:
            self._instruments.setdefault(instrument.name, instrument)
            series = self._totals.setdefault(instrument.name, {})
            key = _label_set(labels)
            series[key] = series.get(key, 0.0) + value

    def record(self, instrument: Instrument, value: float, labels: Mapping[str, str]) -> None:
        """Stores every observation as it came, in order — no buckets."""
        with self._lock:
            self._instruments.setdefault(instrument.name, instrument)
            self._observed.setdefault(instrument.name, {}).setdefault(
                _label_set(labels), []
            ).append(value)

    def instrument(self, name: str) -> Instrument:
        """The instrument recorded under `name` — `KeyError` when none was."""
        return self._instruments[name]

    def names(self) -> list[str]:
        """The names of every instrument measured so far, sorted."""
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
        """Forget every instrument and every measurement — between tests."""
        with self._lock:
            self._instruments.clear()
            self._totals.clear()
            self._observed.clear()
