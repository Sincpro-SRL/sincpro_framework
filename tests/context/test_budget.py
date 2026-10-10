"""The context stays cheap where it is used most (PRD_22 §7): reading it, opening a scope, opening an
execution. The budgets are wide on purpose — they catch a regression of an order of magnitude, never
a slow machine: CI measures under coverage, on a shared runner, several times what a laptop does —
a scope is ~30 µs on a laptop and ~200 µs there — so each is the best of a few rounds, against a
budget an order of magnitude above the laptop."""

import time

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.context import use_context

READS = 20_000


class CommandRead(DataTransferObject):
    pass


def _per_call(work, times: int, rounds: int = 1) -> float:  # type: ignore[no-untyped-def]
    """Seconds per call — the best of `rounds`, so a pause of the machine is not counted."""
    best = float("inf")
    for _ in range(rounds):
        started = time.perf_counter()
        for _ in range(times):
            work()
        best = min(best, (time.perf_counter() - started) / times)
    return best


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

    assert _per_call(scope, 500, rounds=5) < 400e-6
