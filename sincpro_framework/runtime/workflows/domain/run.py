"""What a run did: every step, whether it ran, was skipped or failed, and why.

Context: the trace is the cure for a definition that "silently" did nothing — each step records
what it received, what it answered, how long it took, and the reason it was skipped or failed.
The run records the version of the workflows it ran with.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import Field

from sincpro_framework.sincpro_abstractions import DataTransferObject


class StepStatus(StrEnum):
    RAN = "ran"
    SKIPPED = "skipped"
    FAILED = "failed"


class RunStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class StepRun(DataTransferObject):
    id: str
    status: StepStatus
    reason: str = ""
    """Why it was skipped: the condition, and the values it read."""

    input: Any = None
    output: Any = None
    error: str = ""
    duration_ms: float = 0.0


class WorkflowRun(DataTransferObject):
    workflow: str
    version: str
    status: RunStatus = RunStatus.SUCCEEDED
    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    steps: list[StepRun] = Field(default_factory=list)
    error: str = ""


class WorkflowFailed(Exception):
    """A run stopped — by a `fail` step, a step that failed, or a guard. `run` is its trace."""

    def __init__(self, message: str, run: WorkflowRun) -> None:
        super().__init__(message)
        self.run = run


@dataclass(frozen=True)
class Limits:
    max_steps: int = 1000
    max_items: int = 1000
    max_depth: int = 5


class CommandRunWorkflow(DataTransferObject):
    workflow: str
    input: dict[str, Any] = Field(default_factory=dict)


class ResponseRunWorkflow(DataTransferObject):
    output: dict[str, Any]
    run: WorkflowRun
