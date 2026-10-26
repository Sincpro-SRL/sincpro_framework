"""The process's recorder: chosen once, lazily, by what it is handed, replaceable by code, and the
shield every measurement passes through (PRD_03 §4.5).

    active = ActiveRecorder(from_settings)     which recorder: `services.recording` decides
    active_recorder.emit(issued, 1, {"currency": "BOB"})

Context: metrics belong to the process, as the Prometheus registry and the OTel meter provider
do — every bus of it records into one recorder, told apart by the `sincpro.context` label and
by the context prefix of a declared name. Nothing here raises into a use case: a recorder that
fails is logged once per instrument and skipped.
"""

import threading
from collections.abc import Callable, Mapping

from sincpro_framework.observability import correlation
from sincpro_framework.observability.metrics.domain.instruments import (
    Instrument,
    InstrumentKind,
)
from sincpro_framework.observability.metrics.domain.recorder import Recorder
from sincpro_framework.sincpro_logger import logger

_UNSET = object()


class ActiveRecorder:
    """The one recorder of the process, chosen lazily by `choose`, swappable by code, and the
    shield every measurement passes through (`emit`)."""

    def __init__(self, choose: Callable[[], Recorder | None]) -> None:
        """Nothing chosen yet — `choose` is asked on the first `get`."""
        self._choose = choose
        self._lock = threading.Lock()
        self._chosen: object = _UNSET
        self._failing: set[str] = set()

    def get(self) -> Recorder | None:
        """The recorder in use — what `choose` answers the first time, under the lock so racing
        threads choose once; a choice that raises is logged and leaves metrics off.

            active_recorder.get()   ->  OtelRecorder(...)     # first call: `choose` asked
            active_recorder.get()   ->  the same recorder     # later calls: no lock, `choose` not asked
            active_recorder.get()   ->  None                  # metrics off, or the choice raised

        `set` replaces the choice; `restore` of the "not chosen yet" marker asks `choose` again.
        """
        chosen = self._chosen
        if chosen is _UNSET:
            with self._lock:
                if self._chosen is _UNSET:
                    try:
                        self._chosen = self._choose()
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
        """Put back what `set` answered — even the "not chosen yet" marker, so `choose` is asked
        again after a test that swapped the recorder."""
        with self._lock:
            self._chosen = previous
            self._failing.clear()

    def emit(self, instrument: Instrument, value: float, labels: Mapping[str, str]) -> None:
        """One measurement, shielded: a counter never goes down, and a recorder that raises is
        logged once per instrument and never reaches the use case. Every measurement carries
        the execution's correlation labels (`correlated`).

            active_recorder.emit(issued, 1, {"currency": "BOB"})
                ->  recorder.add(issued + the correlation keys, 1, {"currency": "BOB",
                                 "service.name": "billing-sdk", "tenant": "acme", ...})
            active_recorder.emit(issued, -1, {...})    ->  nothing: a counter never goes down
            a recorder raising on `issued`    ->  one warning for it; the use case goes on

        1. No recorder (metrics off): nothing.
        2. A correlated instrument (the default) takes the correlation keys and their values.
        3. By kind:
           3.1 a histogram records the value;
           3.2 a counter handed a negative value drops it;
           3.3 a counter or an up-down counter adds it.
        Final: whatever raises is swallowed — logged the first time for that instrument's name
        until the recorder is replaced (`set`/`restore` forget it).
        """
        recorder = self.get()
        if recorder is None:
            return
        try:
            if instrument.correlated:
                instrument, labels = correlated(instrument, labels)
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


_extended: dict[tuple[Instrument, tuple[str, ...]], Instrument] = {}


def correlated(
    instrument: Instrument, labels: Mapping[str, str]
) -> tuple[Instrument, dict[str, str]]:
    """`instrument` with the correlation keys among its label keys, and `labels` with their
    values for the execution in progress — what the measurement names itself wins.

    Context: added here, where every instrument passes, so the use case's duration, the
    declared metrics, the instruments by hand, the cache, the idempotency and the queue carry
    them alike, on every backend, with no collector (PRD_03 §4.10).

        correlated(issued, {"currency": "BOB", "tenant": "beta"})
            ->  (issued with label_keys ("currency", "service.name", "service.version",
                 "release", "tenant"),
                 {"service.name": "billing-sdk", "service.version": "1.2.0",
                  "release": "billing-sdk:1.2.0", "currency": "BOB", "tenant": "beta"})

    Only the keys the instrument lacks are added; the extended instrument is built once per
    instrument and set of extra keys, and stored, so a backend sees one shape per series."""
    extra = tuple(
        one for one in correlation.metric_label_keys() if one not in instrument.label_keys
    )
    if not extra:
        return instrument, dict(labels)
    key = (instrument, extra)
    extended = _extended.get(key)
    if extended is None:
        extended = instrument.model_copy(
            update={"label_keys": (*instrument.label_keys, *extra)}
        )
        _extended[key] = extended
    missing = tuple(one for one in extra if one not in labels)
    return extended, {**correlation.metric_labels(missing), **labels}
