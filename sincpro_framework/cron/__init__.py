"""Crons: callers of use cases on a clock, beside the buses and not on them.

cron_payments = Crons("cron-payments")
cron_payments.add_dependency("cybersource", cybersource)

@cron_payments.cron("0 2 * * *", timezone="America/La_Paz")
class Reconcile(Cron):
    cybersource: UseFramework

    def run(self, tick: Tick) -> None: ...

CronProcess(build_crons).start()             # its own process, beside the service
"""

from sincpro_framework.cron.clocks import Clock, InProcessClock, ManualClock
from sincpro_framework.cron.gateway import CronGateway, CronStatus
from sincpro_framework.cron.process import CronProcess
from sincpro_framework.cron.registry import (
    Cron,
    CronDefinition,
    Crons,
    Missed,
    Overlap,
    Tick,
)
from sincpro_framework.cron.runs import CronRuns, InMemoryRuns, Run, RunOutcome
from sincpro_framework.cron.triggers import CronExpression, Every, Trigger

__all__ = [
    "Clock",
    "Cron",
    "CronDefinition",
    "CronExpression",
    "CronGateway",
    "CronProcess",
    "CronRuns",
    "CronStatus",
    "Crons",
    "Every",
    "InProcessClock",
    "ManualClock",
    "InMemoryRuns",
    "Missed",
    "Overlap",
    "Run",
    "RunOutcome",
    "Tick",
    "Trigger",
]
