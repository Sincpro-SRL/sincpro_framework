"""What validation finds in a workflow: one issue each, and the refusal of a first load that found any."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Issue:
    workflow: str
    step: str
    """The step id — empty for the workflow itself."""

    path: str
    """Where in the step: `input.total`, `when`, `code`…"""

    message: str

    def __str__(self) -> str:
        where = ".".join(part for part in (self.step, self.path) if part)
        return f"{self.workflow} {where}: {self.message}"


class WorkflowsInvalid(Exception):
    """The first load found issues: there is no valid set to serve."""

    def __init__(self, issues: list[Issue]) -> None:
        super().__init__("; ".join(str(issue) for issue in issues))
        self.issues = issues
