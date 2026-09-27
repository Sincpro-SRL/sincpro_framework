"""What the framework ships behind the contracts of `domain`."""

from sincpro_framework.cron.adapters.clocks import InProcessClock, ManualClock
from sincpro_framework.cron.adapters.in_memory_runs import InMemoryRuns
from sincpro_framework.cron.adapters.key_value_runs import KeyValueRuns

__all__ = ["InMemoryRuns", "InProcessClock", "KeyValueRuns", "ManualClock"]
