"""`RecorderContract`: what every `Recorder` must do, runnable against a backend of the project's.

    def test_my_recorder_honours_the_contract():
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
    """The rules a `Recorder` follows — methods `check` runs in order; see the module."""

    def __init__(self, recorder: Recorder, total: Read, count: Read) -> None:
        """The recorder under test and the two readers of its backend: `total` for a counter's
        value, `count` for a histogram's number of observations."""
        self.recorder = recorder
        self.total = total
        self.count = count

    def a_counter_adds_up_per_set_of_labels(self) -> None:
        """Proves a counter sums every `add` per set of labels, and tracks each set separately:
        2 + 3 under `ok` is 5, the 1 under `domain` is its own series.

            RecorderContract(InMemoryRecorder(), total, count)
                .a_counter_adds_up_per_set_of_labels()             ->  None: the rule holds
            on a recorder that stores only the last value
                ->  AssertionError("a counter adds up per set of labels")

        Written to `contract.runs`, so a fresh recorder is expected."""
        ok, failed = {"contract.outcome": "ok"}, {"contract.outcome": "domain"}
        self.recorder.add(COUNTER, 2, ok)
        self.recorder.add(COUNTER, 3, ok)
        self.recorder.add(COUNTER, 1, failed)
        assert self.total(COUNTER.name, ok) == 5, "a counter adds up per set of labels"
        assert self.total(COUNTER.name, failed) == 1, "each set of labels is its own series"

    def an_up_down_counter_goes_both_ways(self) -> None:
        """Proves an up-down counter takes a negative `add`: 3 then -2 is 1."""
        self.recorder.add(UP_DOWN, 3, {})
        self.recorder.add(UP_DOWN, -2, {})
        assert self.total(UP_DOWN.name, {}) == 1, "an up-down counter goes down too"

    def a_histogram_counts_its_observations_per_set_of_labels(self) -> None:
        """Proves a histogram records every observation — three values, below, between and above
        its buckets, count as three."""
        labels = {"contract.outcome": "ok"}
        for value in (0.05, 0.5, 5.0):
            self.recorder.record(HISTOGRAM, value, labels)
        assert self.count(HISTOGRAM.name, labels) == 3, "a histogram counts what it observed"

    def concurrent_adds_are_all_kept(self) -> None:
        """Proves no `add` is lost under threads: 8 threads adding 1 two hundred times each
        total 1600.

            RecorderContract(InMemoryRecorder(), total, count)
                .concurrent_adds_are_all_kept()                   ->  None: 1600 recorded
            on a recorder that reads and writes its total without a lock
                ->  AssertionError("no add is lost under threads") when two adds race

        All 8 threads are started before any is joined, so their adds overlap."""
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

    def check(self) -> None:
        """Run every rule against the recorder; the first broken one raises `AssertionError`.

        Context: each rule writes its own series, so a fresh recorder (or registry) is
        expected — run on one already used, the totals would add up to more.
        """
        self.a_counter_adds_up_per_set_of_labels()
        self.an_up_down_counter_goes_both_ways()
        self.a_histogram_counts_its_observations_per_set_of_labels()
        self.concurrent_adds_are_all_kept()
