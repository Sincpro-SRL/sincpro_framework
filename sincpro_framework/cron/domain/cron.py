"""A cron, the tick it answers, and the policies of its registration."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from sincpro_framework.cron.domain.runs import CronRuns
from sincpro_framework.cron.domain.schedule import Trigger


class Overlap(StrEnum):
    """What a tick does while the previous run of its cron has not finished."""

    SKIP = "skip"
    ALLOW = "allow"


class Missed(StrEnum):
    """What happens to ticks that passed while nothing ran — a deploy, an outage."""

    SKIP = "skip"
    RUN_LATEST = "run_latest"
    RUN_ALL = "run_all"


@dataclass(frozen=True)
class Tick:
    """The run in progress: which cron, and the tick it answers."""

    cron: str
    scheduled_for: datetime
    runs: CronRuns = field(repr=False, compare=False)

    def once(self, key: str) -> bool:
        """Context: `True` the first time `key` is asked for this tick, on any replica; `False`
        after — a step that must not be repeated when the tick is. At most once: a step that
        fails after `once` is not retried by the next run of the same tick."""
        return self.runs.claim(self.cron, self.scheduled_for, key)


class Cron(ABC):
    """A cron: its registry injects every dependency it has, as a bus does for its Features;
    `run` is what happens at each tick. One instance serves every tick — state lives in locals.
    """

    @abstractmethod
    def run(self, tick: Tick) -> None: ...


@dataclass(frozen=True)
class CronDefinition:
    name: str
    cron_class: type[Cron]
    trigger: Trigger
    overlap: Overlap
    missed: Missed
    missed_window: timedelta
    stale_after: timedelta
