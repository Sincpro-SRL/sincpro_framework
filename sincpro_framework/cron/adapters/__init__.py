"""What the framework ships behind the contracts of `domain`."""

from sincpro_framework.cron.adapters.clocks import InProcessClock, ManualClock
from sincpro_framework.cron.adapters.in_memory_runs import InMemoryRuns

__all__ = ["InMemoryRuns", "InProcessClock", "ManualClock"]
