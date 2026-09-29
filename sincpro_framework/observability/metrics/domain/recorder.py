"""`Recorder`: the one port a metrics backend implements (PRD_03 §4.5).

    class StatsdRecorder(Recorder):          # a backend of the project's
        def add(self, instrument, value, labels): ...
        def record(self, instrument, value, labels): ...

    metrics.use(StatsdRecorder())

Context: two methods, because a backend has two kinds of instrument — a sum (`add`: counters, up
and down) and a distribution (`record`: histograms). The recorder creates its own object for an
instrument the first time it sees its name, and keeps it. It never raises into the use case:
the framework shields every call, and a recorder only has to be correct.
"""

from abc import ABC, abstractmethod
from collections.abc import Mapping

from sincpro_framework.observability.metrics.domain.instruments import Instrument


class Recorder(ABC):
    @abstractmethod
    def add(self, instrument: Instrument, value: float, labels: Mapping[str, str]) -> None:
        """A counter or an up-down counter moves by `value` for this set of labels."""

    @abstractmethod
    def record(self, instrument: Instrument, value: float, labels: Mapping[str, str]) -> None:
        """A histogram observes `value` for this set of labels."""
