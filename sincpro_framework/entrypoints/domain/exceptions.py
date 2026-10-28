"""What exposing a bus refuses on its own: a service that is not ready to take a call."""

from sincpro_framework.common.failures.problem import Problem
from sincpro_framework.entrypoints.domain.readiness import ReadinessReport
from sincpro_framework.exceptions import ServiceUnavailableError


class NotReady(ServiceUnavailableError):
    """A readiness check did not pass — answered 503 by every HTTP wire, the failing checks named.

    NotReady(report)  ->  503 {"kind": "unavailable", "reason": "UNAVAILABLE",
                               "detail": "not ready: valkey (did not answer within 1.0s)",
                               "checks": [{"name": "valkey", "ready": false, …}, …], …}
    """

    def __init__(self, report: ReadinessReport) -> None:
        """Say which checks failed, and keep the whole `report`."""
        failing = ", ".join(f"{check.name} ({check.detail})" for check in report.failing())
        super().__init__(f"not ready: {failing}")
        self.report = report

    def refine_problem(self, problem: Problem) -> Problem:
        """The checks are what a probe reads: told, with every check's answer beside."""
        return problem.model_copy(
            update={
                "detail": str(self),
                "extensions": {"checks": self.report.model_dump(mode="json")["checks"]},
            }
        )
