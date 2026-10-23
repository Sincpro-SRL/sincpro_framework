"""The event an execution begins with: `ExecutionStarted`."""

from dataclasses import dataclass

from sincpro_framework.bus_pipeline.events import ExecutionEvent


@dataclass(kw_only=True)
class ExecutionStarted(ExecutionEvent):
    """A use case that began — what it was handed, where, in which flow, before it runs."""

    name = "sincpro.execution.v1.started"
