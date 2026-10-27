"""Business metrics count what was done: a dry run and an answer the idempotency replayed are
timed — `sincpro.run` says which — and count, sum and measure nothing."""

from datetime import timedelta

from sincpro_framework import (
    DataTransferObject,
    DryRunnable,
    Feature,
    Preview,
    UseFramework,
)
from sincpro_framework.context import InMemoryContexts
from sincpro_framework.observability.metrics import InMemoryRecorder, field_path, metrics

ANA = {"tenant_id": "acme", "user_id": "ana"}


class WriteCommand(DryRunnable):
    dry_run: bool = True


class CommandCharge(WriteCommand):
    request_id: str
    amount: int


class ResponseCharge(DataTransferObject):
    charged: int


def charges_bus() -> tuple[UseFramework, list[int]]:
    """A bus whose charges count and sum what was charged, run once per request by type."""
    charged: list[int] = []
    bus = UseFramework("billing-metrics-done", log_after_execution=False)
    bus.store_context_in(InMemoryContexts())
    bus.idempotency.declare_for(WriteCommand, expires_after=timedelta(minutes=2))

    @bus.feature(CommandCharge)
    @metrics.counts()
    @metrics.sums(field_path(CommandCharge).amount)
    class Charge(Feature):
        def execute(self, dto: CommandCharge) -> ResponseCharge | Preview:
            if dto.dry_run:
                return Preview(text=f"Charge {dto.amount}")
            charged.append(dto.amount)
            return ResponseCharge(charged=dto.amount)

    return bus, charged


def total(recorder: InMemoryRecorder, ending: str) -> float:
    """The one counter whose name ends with `ending`, summed over its labels."""
    [name] = [one for one in recorder.names() if one.endswith(ending)]
    return sum(recorder.totals(name).values())


def test_a_dry_run_a_run_and_a_replay_count_one_and_sum_once():
    bus, charged = charges_bus()
    recorder = InMemoryRecorder()

    with metrics.using(recorder), bus.context(ANA):
        bus(CommandCharge(request_id="r-1", amount=10))  # a dry run
        bus(CommandCharge(request_id="r-1", amount=10, dry_run=False))
        bus(CommandCharge(request_id="r-1", amount=10, dry_run=False))  # replayed

    assert charged == [10]
    assert total(recorder, ".runs") == 1
    assert total(recorder, ".amount") == 10


def test_the_duration_says_how_each_run_went():
    bus, _ = charges_bus()
    recorder = InMemoryRecorder()

    with metrics.using(recorder), bus.context(ANA):
        bus(CommandCharge(request_id="r-1", amount=10))
        bus(CommandCharge(request_id="r-1", amount=10, dry_run=False))
        bus(CommandCharge(request_id="r-1", amount=10, dry_run=False))

    ran = [
        labels["sincpro.run"]
        for labels, values in recorder.observations("sincpro.use_case.duration")
        for _ in values
    ]
    assert sorted(ran) == ["dry_run", "executed", "replayed"]
