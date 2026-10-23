"""What a workflow raises."""

from typing import TYPE_CHECKING

from sincpro_framework.ddd.exceptions import DomainError, InvalidCriteria
from sincpro_framework.exceptions import ProgrammingError

if TYPE_CHECKING:
    from sincpro_framework.runtime.workflows.domain.issue import Issue
    from sincpro_framework.runtime.workflows.domain.run import WorkflowRun


class WorkflowsInvalid(ProgrammingError):
    """The first load found issues: there is no valid set to serve."""

    def __init__(self, issues: "list[Issue]") -> None:
        super().__init__("; ".join(str(issue) for issue in issues))
        self.issues = issues


class UnresolvedReference(Exception):
    pass


class WorkflowFailed(DomainError):
    """A run stopped — by a `fail` step, a step that failed, or a guard. `run` is its trace."""

    def __init__(self, message: str, run: "WorkflowRun") -> None:
        super().__init__(message)
        self.run = run
