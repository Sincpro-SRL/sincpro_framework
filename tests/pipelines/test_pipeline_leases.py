"""A pipeline that holds a lease is one run of a `run_id` at a time across every replica: a second
execution is refused before it touches the run, a worker that dies leaves its run `abandoned`, and
a worker that lost its lease stops before its next step and writes nothing more."""

import threading
import time
from datetime import UTC, datetime, timedelta

import pytest

from sincpro_framework import ApplicationService, DataTransferObject, Feature, UseFramework
from sincpro_framework.context import InMemoryContexts, use_context
from sincpro_framework.pipelines import Pipeline, PipelineRun, PipelineState
from sincpro_framework.store import LeaseState
from sincpro_framework.store.domain import exceptions as store_exceptions

PIPELINE = "leased-pipelines.CommandRunNightly"


class CommandRunNightly(DataTransferObject):
    run_id: str


class CommandCollect(DataTransferObject):
    run_id: str


class CommandBill(DataTransferObject):
    run_id: str


class CommandClose(DataTransferObject):
    run_id: str
    ok: bool
    failure: str


def nightly(
    contexts: InMemoryContexts, during_collect=lambda: None
) -> tuple[UseFramework, list[str]]:
    did: list[str] = []
    bus = UseFramework("leased-pipelines", log_after_execution=False)
    bus.store_context_in(contexts)

    @bus.feature(CommandCollect)
    class Collect(Feature):
        def execute(self, dto: CommandCollect) -> None:
            did.append("collect")
            during_collect()

    @bus.feature(CommandBill)
    class Bill(Feature):
        def execute(self, dto: CommandBill) -> None:
            did.append("bill")

    @bus.feature(CommandClose)
    class Close(Feature):
        def execute(self, dto: CommandClose) -> None:
            did.append("close")

    class RunNightly(Pipeline, ApplicationService):
        steps = [CommandCollect, CommandBill]
        always = CommandClose
        lease = timedelta(seconds=0.3)

    bus.app_service(CommandRunNightly)(RunNightly)
    return bus, did


def lease_state(contexts: InMemoryContexts, run_id: str) -> LeaseState:
    with use_context().scoped({}) as context:
        runs = context.pipelines(store=contexts)
        return contexts.leases().state(runs.lease_name(PIPELINE, run_id))


def test_a_run_is_driven_under_its_lease_and_frees_it_when_it_ends():
    contexts = InMemoryContexts()
    bus, did = nightly(contexts)

    run = bus(CommandRunNightly(run_id="2026-10-23"), PipelineRun)

    assert run.state is PipelineState.SUCCEEDED and did == ["collect", "bill", "close"]
    assert lease_state(contexts, "2026-10-23") is LeaseState.FREE


def test_a_second_execution_of_the_same_run_is_refused_before_it_runs_a_step():
    contexts = InMemoryContexts()
    collecting, release = threading.Event(), threading.Event()

    def slow() -> None:
        collecting.set()
        release.wait(5)

    bus, did = nightly(contexts, during_collect=slow)
    first = threading.Thread(target=lambda: bus(CommandRunNightly(run_id="R-1")))
    first.start()
    collecting.wait(5)
    try:
        with pytest.raises(store_exceptions.LeaseTaken):
            bus(CommandRunNightly(run_id="R-1"))
    finally:
        release.set()
        first.join()

    assert did == ["collect", "bill", "close"]  # the second never ran a step


def test_a_run_whose_worker_died_is_found_abandoned():
    now = datetime(2026, 10, 23, tzinfo=UTC)
    clock = [now]
    contexts = InMemoryContexts(now=lambda: clock[0])
    with use_context().scoped({}) as context:
        runs = context.pipelines(store=contexts)
        runs.save(PipelineRun(pipeline=PIPELINE, run_id="R-2", started_at=now))
        contexts.leases().take(
            runs.lease_name(PIPELINE, "R-2"), owner="worker-a", ttl=timedelta(minutes=2)
        )
        assert runs.abandoned(PIPELINE) == []

        clock[0] = now + timedelta(minutes=3)  # the worker stopped renewing it

        assert [one.run_id for one in runs.abandoned(PIPELINE)] == ["R-2"]


def test_a_worker_that_lost_its_lease_stops_before_its_next_step_and_writes_nothing_more():
    contexts = InMemoryContexts()

    def stolen() -> None:
        with use_context().scoped({}) as context:
            name = context.pipelines(store=contexts).lease_name(PIPELINE, "R-3")
        leases = contexts.leases()
        leases.store.delete(leases.claim_key(name))  # its time passed, as in a long pause
        assert leases.take(name, owner="worker-b", ttl=timedelta(minutes=5)) is not None
        time.sleep(0.25)  # the renewer finds it taken

    bus, did = nightly(contexts, during_collect=stolen)

    with pytest.raises(store_exceptions.LeaseLost):
        bus(CommandRunNightly(run_id="R-3"))

    assert did == ["collect"]  # no bill, and no close: the run is another worker's now
    with use_context().scoped({}) as context:
        kept = context.pipelines(store=contexts).read(PIPELINE, "R-3")
    assert kept is not None and kept.state is PipelineState.RUNNING
