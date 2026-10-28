"""What a readiness probe answers: each check by name — ready or not, why, and how long it took — and
whether the service takes traffic.

    ReadinessReport(ready=False, checks=[
        CheckReport(name="buses", ready=True, seconds=0.0),
        CheckReport(name="valkey", ready=False, detail="did not answer within 1.0s", seconds=1.0),
    ])

Context: a service is **alive** while its process can answer and every bus is built — a restart is
what fixes it otherwise; it is **ready** while every check passes — what it needs to answer a call
(a binary, a key, a store, a service it calls). A probe that fails readiness takes the replica out
of the balancer until it passes again, nothing restarted.
"""

from sincpro_framework.sincpro_abstractions import DataTransferObject


class CheckReport(DataTransferObject):
    """One readiness check, as it answered."""

    name: str
    ready: bool
    detail: str | None = None
    """Why it is not ready — what it raised, that it answered `False`, or that it did not answer
    in its time."""
    seconds: float
    """How long it took — its timeout when it did not answer."""


class ReadinessReport(DataTransferObject):
    """Every readiness check of a gateway, as they answered: ready when each one is."""

    ready: bool
    checks: list[CheckReport]

    def failing(self) -> list[CheckReport]:
        """The checks that are not ready, in the order they were added."""
        return [check for check in self.checks if not check.ready]
