"""The context stays cheap where it is used most (PRD_22 §7): reading it, opening a scope, opening an
execution. The budgets are wide on purpose — they catch a regression of an order of magnitude, never
a slow machine."""

import time

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.context import use_context

READS = 20_000


class CommandRead(DataTransferObject):
    pass


def _per_call(work, times: int) -> float:  # type: ignore[no-untyped-def]
    started = time.perf_counter()
    for _ in range(times):
        work()
    return (time.perf_counter() - started) / times


def test_reading_the_context_in_a_handler_costs_microseconds():
    bus = UseFramework("context-budget-read", log_after_execution=False)
    measured: list[float] = []

    @bus.feature(CommandRead)
    class Read(Feature):
        def execute(self, dto: CommandRead) -> None:
            context = use_context()
            measured.append(_per_call(lambda: context["tenant_id"], READS))
            measured.append(_per_call(use_context, READS // 4))

    with bus.context({"tenant_id": "acme", "lang": "es", "tz": "America/La_Paz"}):
        bus(CommandRead())

    a_read, asking = measured
    assert a_read < 2e-6  # a dict lookup
    assert asking < 50e-6  # the node's view, folded once


def test_opening_a_scope_costs_microseconds():
    bus = UseFramework("context-budget-scope", log_after_execution=False)

    def scope() -> None:
        with bus.context({"tenant_id": "acme"}):
            pass

    assert _per_call(scope, 2_000) < 200e-6
