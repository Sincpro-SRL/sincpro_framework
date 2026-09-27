"""Crons: callers of use cases on a clock, beside the buses and not on them.

    cron_payments = Crons[CronDependencyContextType]("cron-payments")
    cron_payments.add_dependency("cybersource", cybersource)

    @cron_payments.cron("0 2 * * *", timezone="America/La_Paz")
    class Reconcile(Cron):
        def run(self, tick: Tick) -> None: ...

    CronProcess(build_crons).start()             # its own process, beside the service

`domain/` holds the vocabulary and the contracts, `adapters/` what the framework ships behind
them, `registry` the `Crons` of a bounded context, `entrypoint/` how they run. This package answers
what a project uses; `ManualClock`, for tests, is in `sincpro_framework.testing`.
"""

from sincpro_framework.cron.adapters import InMemoryRuns, KeyValueRuns
from sincpro_framework.cron.domain import (
    Cron,
    CronRuns,
    Missed,
    Overlap,
    Run,
    RunOutcome,
    Tick,
)
from sincpro_framework.cron.entrypoint import CronGateway, CronProcess, CronStatus
from sincpro_framework.cron.registry import Crons

__all__ = [
    "Cron",
    "CronGateway",
    "CronProcess",
    "CronRuns",
    "CronStatus",
    "Crons",
    "InMemoryRuns",
    "KeyValueRuns",
    "Missed",
    "Overlap",
    "Run",
    "RunOutcome",
    "Tick",
]
