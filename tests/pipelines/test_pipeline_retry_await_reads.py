"""What a pipeline grows into when it needs to: a step retried when its failure is worth it, a run
that waits for a decision and goes on with it, and the runs read through the bus."""

from typing import Any

import pytest

from sincpro_framework import ApplicationService, DataTransferObject, Feature, UseFramework
from sincpro_framework.context import InMemoryContexts
from sincpro_framework.ddd import exceptions as ddd_exceptions
from sincpro_framework.exceptions import FailureKind
from sincpro_framework.pipelines import (
    Await,
    FindRuns,
    GetRun,
    Pipeline,
    PipelineReads,
    PipelineRun,
    PipelineState,
    ResponseRuns,
    Retry,
    StepStatus,
)
from sincpro_framework.pipelines import exceptions as pipelines_exceptions


class CommandRunIssue(DataTransferObject):
    run_id: str = ""
    approval: str = ""


class CommandRefine(DataTransferObject):
    run_id: str


class CommandImplement(DataTransferObject):
    run_id: str
    approval: str


class CommandClose(DataTransferObject):
    run_id: str
    ok: bool
    failure: str


class Unavailable(Exception):
    failure_kind = FailureKind.UNAVAILABLE
    retry_after = 0


class QueryIssueRun(GetRun[CommandRunIssue]): ...


class QueryIssueRuns(FindRuns[CommandRunIssue]): ...


def forge(
    fails: list[Exception] | None = None, retry: Retry | None = None
) -> tuple[UseFramework, list[str]]:
    """A bus whose pipeline refines, waits for an approval, then implements — the refine step
    raising what `fails` holds, one per attempt."""
    did: list[str] = []
    pending = list(fails or [])
    bus = UseFramework("forge-pipelines", log_after_execution=False)
    bus.store_context_in(InMemoryContexts())

    @bus.feature(CommandRefine)
    class Refine(Feature):
        def execute(self, dto: CommandRefine) -> None:
            if pending:
                raise pending.pop(0)
            did.append("refine")

    @bus.feature(CommandImplement)
    class Implement(Feature):
        def execute(self, dto: CommandImplement) -> None:
            did.append(f"implement {dto.approval}")

    @bus.feature(CommandClose)
    class Close(Feature):
        def execute(self, dto: CommandClose) -> None:
            did.append(f"close ok={dto.ok}")

    class RunIssue(Pipeline, ApplicationService):
        steps = [CommandRefine, Await("approval"), CommandImplement]
        always = CommandClose

    RunIssue.retry = retry
    bus.app_service(CommandRunIssue)(RunIssue)

    @bus.feature([QueryIssueRun, QueryIssueRuns])
    class IssueRuns(PipelineReads): ...

    return bus, did


# --- retry ------------------------------------------------------------------------------------


def test_a_step_that_failed_in_a_way_worth_it_is_run_again():
    bus, did = forge(
        fails=[Unavailable("siat"), Unavailable("siat")], retry=Retry(attempts=3)
    )

    run = bus(CommandRunIssue(run_id="i-1"), PipelineRun)

    assert did == ["refine"]
    assert run.state is PipelineState.WAITING
    assert run.steps[0].status is StepStatus.SUCCEEDED and run.steps[0].attempts == 3


def test_retries_run_out_and_the_run_fails_with_the_last_error():
    bus, did = forge(fails=[Unavailable("a"), Unavailable("b")], retry=Retry(attempts=2))

    with pytest.raises(Unavailable):
        bus(CommandRunIssue(run_id="i-2"))

    [run] = bus.current_context().pipelines().find("forge-pipelines.CommandRunIssue")
    assert run.state is PipelineState.FAILED and run.steps[0].attempts == 2
    assert did == ["close ok=False"]


def test_a_rule_of_the_business_is_never_retried():
    bus, did = forge(fails=[ddd_exceptions.DomainError("no credit")], retry=Retry())

    with pytest.raises(ddd_exceptions.DomainError):
        bus(CommandRunIssue(run_id="i-3"))

    assert did == ["close ok=False"]


def test_without_retry_nothing_is_run_again():
    bus, _ = forge(fails=[Unavailable("siat")])

    with pytest.raises(Unavailable):
        bus(CommandRunIssue(run_id="i-4"))


def test_the_wait_grows_and_stops_at_its_bound():
    from datetime import timedelta

    retry = Retry(backoff=3.0, max_wait=timedelta(seconds=5))

    assert [retry.wait_before(attempt, None).total_seconds() for attempt in (1, 2, 3)] == [
        1.0,
        3.0,
        5.0,
    ]
    assert retry.wait_before(1, timedelta(seconds=2)) == timedelta(seconds=2)


# --- await -------------------------------------------------------------------------------------


def test_a_run_waits_for_its_decision_and_goes_on_with_it():
    bus, did = forge()

    waiting = bus(CommandRunIssue(run_id="i-5"), PipelineRun)
    assert (waiting.state, waiting.waiting_for) == (PipelineState.WAITING, "approval")
    assert did == ["refine"]  # always waits too: the run has not ended

    done = bus(CommandRunIssue(run_id="i-5", approval="approved"), PipelineRun)

    assert did == ["refine", "implement approved", "close ok=True"]
    assert done.state is PipelineState.SUCCEEDED
    assert done.decisions == {"approval": "approved"}
    assert [one.status for one in done.steps] == [StepStatus.SUCCEEDED] * 4


def test_a_waiting_run_called_again_without_its_decision_still_waits():
    bus, did = forge()
    bus(CommandRunIssue(run_id="i-6"))

    again = bus(CommandRunIssue(run_id="i-6"), PipelineRun)

    assert again.state is PipelineState.WAITING and did == ["refine"]


def test_the_runs_waiting_for_a_decision_are_found_by_their_state():
    bus, _ = forge()
    bus(CommandRunIssue(run_id="i-7"))
    bus(CommandRunIssue(run_id="i-8", approval="approved"))

    waiting = bus(QueryIssueRuns(state=PipelineState.WAITING), ResponseRuns)

    assert [one.run_id for one in waiting.runs] == ["i-7"]


# --- reads -------------------------------------------------------------------------------------


def test_one_run_is_read_by_its_id():
    bus, _ = forge()
    bus(CommandRunIssue(run_id="i-9"))

    run = bus(QueryIssueRun(run_id="i-9"), PipelineRun)

    assert (run.run_id, run.state) == ("i-9", PipelineState.WAITING)


def test_a_run_not_kept_is_not_found():
    bus, _ = forge()

    with pytest.raises(pipelines_exceptions.RunNotFound) as raised:
        bus(QueryIssueRun(run_id="never"))

    assert raised.value.failure_kind is FailureKind.NOT_FOUND


def test_a_read_that_names_no_pipeline_says_how():
    class QueryWrong(GetRun): ...  # type: ignore[type-arg]

    with pytest.raises(
        pipelines_exceptions.ProgrammingError, match=r"GetRun\[CommandRunNightly\]"
    ):
        from sincpro_framework.pipelines.reads import pipeline_of

        pipeline_of(QueryWrong)


def test_an_await_its_input_cannot_answer_is_a_programming_error():
    bus = UseFramework("forge-await-wrong", log_after_execution=False)
    bus.store_context_in(InMemoryContexts())

    class CommandRun(DataTransferObject):
        run_id: str = ""

    class Waits(Pipeline, ApplicationService):
        steps = [Await("approval")]

    bus.app_service(CommandRun)(Waits)

    with pytest.raises(pipelines_exceptions.ProgrammingError, match="no field 'approval'"):
        bus(CommandRun(run_id="w-1"))


def test_reading_runs_needs_nothing_but_the_context(caplog: Any):
    bus, _ = forge()

    assert bus(QueryIssueRuns(), ResponseRuns).runs == []
