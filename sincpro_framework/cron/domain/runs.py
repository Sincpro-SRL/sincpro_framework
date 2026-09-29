"""The record of runs: the once-per-tick guard, and what the next tick is decided from.

Context: `claim(name, scheduled_for, key)` is the whole mechanism — the first caller gets `True`
and runs; every other caller, on this replica or another, gets `False` and does nothing.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from enum import StrEnum

from pydantic import ConfigDict

from sincpro_framework.deprecations import PositionalFields
from sincpro_framework.sincpro_abstractions import DataTransferObject


class RunOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class Run(PositionalFields, DataTransferObject):
    """One claimed tick — a DTO because the shared record keeps it as JSON."""

    model_config = ConfigDict(frozen=True)

    name: str
    scheduled_for: datetime
    key: str
    started_at: datetime
    finished_at: datetime | None = None
    outcome: RunOutcome | None = None


class CronRuns(ABC):
    """The once-per-tick guard, and the record the next tick is decided from.

    Context: `claim(name, scheduled_for, key)` is the whole mechanism — the first caller gets
    `True` and runs; every other caller, on this replica or another, gets `False` and does
    nothing. An abstract class rather than a `Protocol`, like `Repository`: structural typing
    checks names, not signatures, so an implementation with the wrong arguments would pass.
    """

    @abstractmethod
    def claim(self, name: str, scheduled_for: datetime, key: str = "") -> bool: ...

    @abstractmethod
    def finish(
        self, name: str, scheduled_for: datetime, key: str, outcome: RunOutcome
    ) -> None: ...

    @abstractmethod
    def running(self, name: str, since: datetime) -> bool:
        """Whether a run of this cron for a tick at or after `since` was claimed and has not
        finished. Context: an older unfinished run is presumed dead — its replica crashed or was
        terminated — and no longer holds the cron."""
        ...

    @abstractmethod
    def last(self, name: str) -> Run | None:
        """The run of the latest tick — the cron's own, not a key claimed inside it."""
        ...

    @abstractmethod
    def last_success(self, name: str) -> Run | None: ...
