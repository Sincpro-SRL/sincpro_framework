"""`RecorderContract`: what every `Recorder` must do, runnable against a backend of the project's.

    def test_my_recorder_keeps_the_contract():
        recorder = StatsdRecorder(...)
        RecorderContract(recorder, total=read_counter, count=read_histogram_count).check()

Context: pytest-free — a failed rule raises `AssertionError` naming it. `total(name, labels)` and
`count(name, labels)` read the backend back: a counter's value, a histogram's number of
observations, for one set of labels. Names reach them as the framework wrote them (dotted); the
reader translates, as the recorder does.
"""

import threading
from collections.abc import Callable, Mapping

from sincpro_framework.observability.metrics.domain.instruments import (
    SECONDS,
    Instrument,
    InstrumentKind,
)
from sincpro_framework.observability.metrics.domain.recorder import Recorder

type Read = Callable[[str, Mapping[str, str]], float]

COUNTER = Instrument(
    name="contract.runs", kind=InstrumentKind.COUNTER, label_keys=("contract.outcome",)
)
UP_DOWN = Instrument(name="contract.in_flight", kind=InstrumentKind.UP_DOWN)
HISTOGRAM = Instrument(
    name="contract.duration",
    kind=InstrumentKind.HISTOGRAM,
    unit=SECONDS,
    label_keys=("contract.outcome",),
    buckets=(0.1, 1.0),
)


class RecorderContract:
    def __init__(self, recorder: Recorder, total: Read, count: Read) -> None:
        self.recorder = recorder
        self.total = total
        self.count = count

    def check(self) -> None:
        self.a_counter_adds_up_per_set_of_labels()
        self.an_up_down_counter_goes_both_ways()
        self.a_histogram_counts_its_observations_per_set_of_labels()
        self.concurrent_adds_are_all_kept()

    def a_counter_adds_up_per_set_of_labels(self) -> None:
        ok, failed = {"contract.outcome": "ok"}, {"contract.outcome": "domain"}
        self.recorder.add(COUNTER, 2, ok)
        self.recorder.add(COUNTER, 3, ok)
        self.recorder.add(COUNTER, 1, failed)
        assert self.total(COUNTER.name, ok) == 5, "a counter adds up per set of labels"
        assert self.total(COUNTER.name, failed) == 1, "each set of labels is its own series"

    def an_up_down_counter_goes_both_ways(self) -> None:
        self.recorder.add(UP_DOWN, 3, {})
        self.recorder.add(UP_DOWN, -2, {})
        assert self.total(UP_DOWN.name, {}) == 1, "an up-down counter goes down too"

    def a_histogram_counts_its_observations_per_set_of_labels(self) -> None:
        labels = {"contract.outcome": "ok"}
        for value in (0.05, 0.5, 5.0):
            self.recorder.record(HISTOGRAM, value, labels)
        assert self.count(HISTOGRAM.name, labels) == 3, "a histogram counts what it observed"

    def concurrent_adds_are_all_kept(self) -> None:
        labels = {"contract.outcome": "concurrent"}

        def add() -> None:
            for _ in range(200):
                self.recorder.add(COUNTER, 1, labels)

        threads = [threading.Thread(target=add) for _ in range(8)]
        for one in threads:
            one.start()
        for one in threads:
            one.join()
        assert self.total(COUNTER.name, labels) == 1600, "no add is lost under threads"
