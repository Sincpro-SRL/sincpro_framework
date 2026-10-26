"""The record of runs: the once-per-tick guard, and what the next tick is decided from.

Context: `claim(name, scheduled_for, key)` is the whole mechanism — the first caller gets `True`
and runs; every other caller, on this replica or another, gets `False` and does nothing.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from enum import StrEnum

from pydantic import ConfigDict

from sincpro_framework.sincpro_abstractions import DataTransferObject


class RunOutcome(StrEnum):
    """How a claimed tick ended — `SKIPPED` when it was claimed but not run, because the
    previous run still held the cron (`overlap=SKIP`)."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class Run(DataTransferObject):
    """One claimed tick — a DTO because the shared record stores it as JSON."""

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
    def claim(self, name: str, scheduled_for: datetime, key: str = "") -> bool:
        """`True` for the first caller of `(name, scheduled_for, key)`, `False` for every
        other — atomic across whoever shares this record.

            claim("cron-payments.Reconcile", at_0200)            ->  True   (this one runs)
            claim("cron-payments.Reconcile", at_0200)            ->  False  (already claimed)
            claim("cron-payments.Reconcile", at_0200, "notify")  ->  True   (`Tick.once`)

        Context: `key=""` is the cron's own run of the tick; any other key is a step claimed
        inside it, which `last`, `running` and `last_success` never count.
        """
        ...

    @abstractmethod
    def finish(
        self, name: str, scheduled_for: datetime, key: str, outcome: RunOutcome
    ) -> None:
        """Record how a claimed run ended — its `finished_at` and `outcome` — so it no longer
        holds the cron and `last` / `last_success` see it."""
        ...

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
    def last_success(self, name: str) -> Run | None:
        """The latest run of the cron that ended `SUCCEEDED` — what a status page answers to
        "when did it last work"."""
        ...
