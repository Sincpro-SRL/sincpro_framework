"""Incomes: a use case begins, announced as an event anyone may listen to.

    @billing.on_start                                    a function, in this process
    def began(started: ExecutionStarted) -> None: ...

    billing.publish_starts(to=Publisher(queue))          a queue: another bus, a broker topic
    starts.subscribe(audit)                              every bus of the process

An income says what a use case was handed, before anything runs; how it ended is the outcome's
(`sincpro_framework.bus_pipeline.outcomes`). It is announced for every Feature and ApplicationService, the
Features an ApplicationService runs included, once the context providers have run — so `context`
holds what they derived — and **before the interceptors**: the income is the call as its caller
made it. A use case that never gets that far, with a `requires_context` that is not met, announces
none. Its `execution_id` is the one the completion or the failure of the same execution names.

Like every listener it observes: it never changes what the call answers, a listener that raises is
logged and ignored, and with nobody listening nothing is built.

Context: the event loads with the first who asks for it — a process nobody listens in never loads
it, nor the DDD layer it is built on.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sincpro_framework.bus_pipeline.incomes.events import ExecutionStarted


def __getattr__(name: str) -> Any:
    """`ExecutionStarted`, imported on first use — see the module's last note."""
    if name == "ExecutionStarted":
        from sincpro_framework.bus_pipeline.incomes import events

        return events.ExecutionStarted
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["ExecutionStarted"]
