"""A run of a pipeline, as the context stores it: what it was given and how far it got.

    PipelineRun(pipeline, run_id, input, state, steps=[StepState(command, status, answer, …)], …)

It holds the progress of a run and what each step that succeeded answered, as JSON values — what
fills the steps after it, also when the run goes on in another request.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field

from sincpro_framework.data_layer.store import Lease
from sincpro_framework.pipelines.domain import exceptions
from sincpro_framework.sincpro_abstractions import DataTransferObject


class PipelineState(StrEnum):
    """Where a run stands as a whole — what `FindRuns(state=...)` filters by."""

    RUNNING = "running"
    WAITING = "waiting"
    """Stopped at an `Await`, until the same run is run again with what it waits for."""
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class StepStatus(StrEnum):
    """Where one step of a run stands — `WAITING` only for an `Await` step."""

    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class StepState(DataTransferObject):
    """How one step of a run stands."""

    command: str
    """The step by its identity — `billing.CommandVerifyRun`."""
    status: StepStatus = StepStatus.PENDING
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str = ""
    """The message of what made it fail; never what it answered."""
    kind: exceptions.FailureKind | None = None
    """What kind of failure, as every wire classifies it: what decides whether to try again."""
    retry_after: float | None = None
    attempts: int = 0
    """How many times it was started — more than one when the pipeline retried it."""
    answer: dict[str, Any] = Field(default_factory=dict)
    """What it answered when it succeeded, as JSON values — the fields a step after it is filled
    with; empty for a step that answered nothing."""


class PipelineRun(DataTransferObject):
    """One run of a pipeline: what it was given and how far it got."""

    pipeline: str
    """The pipeline by the identity of its Command — `billing.CommandRunNightly`."""
    run_id: str
    """What names this run: the same one, run again, continues it."""
    owner: str = ""
    """Whose it is — what the context said when it was saved, `tenant_id=acme`; empty when it
    said nothing."""
    version: str = ""
    """A hash of the recipe it ran: a run continued under another recipe starts over."""
    input_name: str = ""
    """The input by its identity — `billing.CommandRunNightly` — to rebuild it by name."""
    input: dict[str, Any] = Field(default_factory=dict)
    """The input as JSON values."""
    state: PipelineState = PipelineState.RUNNING
    current: str | None = None
    """The step it is on, by its identity; `None` when none is."""
    steps: list[StepState] = Field(default_factory=list)
    error: str = ""
    """Why it failed: the message of the step or of what escaped; empty otherwise."""
    waiting_for: str | None = None
    """The `Await` it stopped at, while it is `WAITING`."""
    decisions: dict[str, Any] = Field(default_factory=dict)
    """What each `Await` it passed was answered with, by its name."""
    correlation_id: str | None = None
    """The flow of the execution that ran it, to follow it in logs and traces."""
    started_at: datetime
    finished_at: datetime | None = None
    store_version: int = 0
    """What the store says: the version to hand back as `expected`. Opaque: compare it, never
    count with it."""


@dataclass
class RunInProgress:
    """The run an execution drives, as it was last saved — on the node of the pipeline's execution,
    under this type, so its steps write over it: one write per change, and no read."""

    run: PipelineRun
    waiting_for: str | None = None
    """The `Await` the pipeline stopped at — what the recorder closes the run as waiting for."""
    lease: Lease | None = None
    """The lease the run is driven under, when the pipeline holds one."""
    resumed_from: str | None = None
    """The `Await` the run was waiting at when this execution took it up — the only one whose
    decision this call's input may give."""
    decided: dict[str, Any] = field(default_factory=dict)
    """What each `Await` this run passed was answered with — the earlier ones and the one
    decided now — what the recorder saves as the run's `decisions`."""
