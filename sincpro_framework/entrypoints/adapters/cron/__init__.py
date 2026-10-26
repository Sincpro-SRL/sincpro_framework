"""Crons: callers of use cases on a clock, beside the buses and not on them.

    cron_payments = Crons[CronDependencyContextType]("cron-payments")
    cron_payments.add_dependency("cybersource", cybersource)

    @cron_payments.cron("0 2 * * *", timezone="America/La_Paz")
    class Reconcile(Cron):
        def run(self, tick: Tick) -> None: ...

    CronProcess(build_crons).start()             # its own process, beside the service

`domain/` holds the vocabulary and the contracts, `adapters/` what the framework ships behind
them, `registry` the `Crons` of a bounded context, `gateway/` how they run. This package answers
what a project uses; `ManualClock`, for tests, is in `sincpro_framework.runtime.testing`.
"""

from sincpro_framework.entrypoints.adapters.cron.adapters.in_memory_runs import InMemoryRuns
from sincpro_framework.entrypoints.adapters.cron.adapters.key_value_runs import KeyValueRuns
from sincpro_framework.entrypoints.adapters.cron.domain.cron import (
    Cron,
    Missed,
    Overlap,
    Tick,
)
from sincpro_framework.entrypoints.adapters.cron.domain.runs import CronRuns, Run, RunOutcome
from sincpro_framework.entrypoints.adapters.cron.gateway.gateway import (
    CronGateway,
    CronStatus,
)
from sincpro_framework.entrypoints.adapters.cron.gateway.process import CronProcess
from sincpro_framework.entrypoints.adapters.cron.registry import Crons

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
