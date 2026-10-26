"""A `Pipeline` is an ApplicationService that is the order of its steps: declared as data, filled
from its input by the names of the fields, and kept in the context — how far each run got — by an
interceptor the bus attaches. Nothing in it is forced: it serves a system with no tenant and no
user as well as a user across tenants, and leaves the recorder out when told."""

from collections.abc import Callable
from enum import StrEnum
from typing import Any

import pytest

from sincpro_framework import (
    ApplicationService,
    DataTransferObject,
    Feature,
    UseFramework,
    exceptions,
)
from sincpro_framework.bus_pipeline.outcomes.events import ExecutionCompleted, ExecutionFailed
from sincpro_framework.bus_pipeline.outcomes.listeners import (
    completion_listeners,
    failure_listeners,
)
from sincpro_framework.context import InMemoryContexts, use_context
from sincpro_framework.context.domain import exceptions as context_exceptions
from sincpro_framework.context.domain.keys import ContextKey
from sincpro_framework.context.domain.owner import Owner
from sincpro_framework.pipelines import (
    Pipeline,
    PipelineRun,
    PipelineRuns,
    PipelineState,
    Resume,
    StepStatus,
)
from sincpro_framework.pipelines.entrypoint import pipeline as pipeline_module
from sincpro_framework.pipelines.services import recorder as recorder_module


class CommandRunNightly(DataTransferObject):
    run_id: str = ""
    day: str = "today"


class CommandCollect(DataTransferObject):
    run_id: str


class CommandBill(DataTransferObject):
    run_id: str
    day: str


class CommandNotify(DataTransferObject):
    run_id: str


class CommandClose(DataTransferObject):
    run_id: str
    ok: bool
    failure: str


class StepAnswer(DataTransferObject):
    ok: bool = True
    summary: str = ""


class SiatDown(Exception):
    failure_kind = exceptions.FailureKind.UNAVAILABLE
    retry_after = 30


@pytest.fixture(autouse=True)
def nobody_listens_after():
    yield
    completion_listeners.clear()
    failure_listeners.clear()


class Shop:
    """A bus with a pipeline, and what its steps did."""

    def __init__(
        self,
        name: str,
        steps: Any = (CommandCollect, CommandBill, CommandNotify),
        answers: dict[type, Callable[[Any], Any]] | None = None,
        store: InMemoryContexts | None = None,
        entry: type = CommandRunNightly,
        **declared: Any,
    ) -> None:
        self.calls: list[str] = []
        self.contexts: list[dict[str, Any]] = []
        self.store = store if store is not None else InMemoryContexts()
        self.bus = UseFramework(name, log_after_execution=False)
        self.bus.store_context_in(self.store)
        calls, contexts = self.calls, self.contexts
        answers = answers or {}

        def feature(command: type, answer: Callable[[Any], Any] | None = None) -> None:
            @self.bus.feature(command)
            class Step(Feature):
                def execute(self, dto: Any) -> Any:
                    calls.append(type(dto).__name__)
                    contexts.append(dict(self.context))
                    behave = answers.get(command, answer)
                    return behave(dto) if behave is not None else StepAnswer()

            Step.__name__ = f"Run{command.__name__}"

        for command in (CommandCollect, CommandBill, CommandNotify):
            feature(command)

        @self.bus.feature(CommandClose)
        class Close(Feature):
            def execute(self, dto: CommandClose) -> None:
                calls.append(f"close ok={dto.ok} failure={dto.failure!r}")

        body = {"steps": steps, **declared}
        self.pipeline = type("RunNightly", (Pipeline, ApplicationService), body)
        self.command = entry
        self.bus.app_service(entry)(self.pipeline)

    def run(self, **fields: Any) -> Any:
        return self.bus(self.command(**fields))

    def runs(self, scope: dict[str, Any] | None = None, **ask: Any) -> list[PipelineRun]:
        with self.bus.context(scope or {}):
            return PipelineRuns.of(**ask).find(f"{self.bus.name}.{self.command.__name__}")


def test_a_pipeline_runs_its_steps_in_order_filled_by_the_names_of_the_fields():
    shop = Shop("pipe-order")

    answer = shop.run(run_id="r-1", day="monday")

    assert shop.calls == ["CommandCollect", "CommandBill", "CommandNotify"]
    assert isinstance(answer, PipelineRun)
    assert (answer.state, answer.run_id) == (PipelineState.SUCCEEDED, "r-1")
    assert [(one.command, one.status) for one in answer.steps] == [
        ("pipe-order.CommandCollect", StepStatus.SUCCEEDED),
        ("pipe-order.CommandBill", StepStatus.SUCCEEDED),
        ("pipe-order.CommandNotify", StepStatus.SUCCEEDED),
    ]
    assert answer.input == {"run_id": "r-1", "day": "monday"}
    assert answer.input_name == "pipe-order.CommandRunNightly"
    assert all(one.started_at and one.finished_at for one in answer.steps)


def test_the_run_is_kept_in_the_context_beside_the_drafts():
    shop = Shop("pipe-kept")
    shop.run(run_id="r-1")

    [kept] = shop.runs()

    assert (kept.run_id, kept.state) == ("r-1", PipelineState.SUCCEEDED)
    assert kept.finished_at is not None and kept.current is None
    assert kept.correlation_id and kept.store_version > 1


def test_a_step_that_answers_not_ok_ends_the_run_failed_and_the_last_command_still_runs():
    shop = Shop(
        "pipe-notok",
        answers={CommandBill: lambda dto: StepAnswer(ok=False, summary="no credit")},
        always=CommandClose,
    )

    answer = shop.run(run_id="r-1")

    assert shop.calls == [
        "CommandCollect",
        "CommandBill",
        "close ok=False failure='no credit'",
    ]
    assert (answer.state, answer.error) == (PipelineState.FAILED, "no credit")
    statuses = {one.command.split(".")[-1]: one.status for one in answer.steps}
    assert statuses["CommandBill"] is StepStatus.FAILED
    assert statuses["CommandNotify"] is StepStatus.PENDING
    assert statuses["CommandClose"] is StepStatus.SUCCEEDED


def test_a_step_that_raises_is_recorded_with_its_kind_and_the_exception_goes_up():
    def breaks(dto: Any) -> Any:
        raise SiatDown("SIAT is not answering")

    shop = Shop("pipe-raises", answers={CommandBill: breaks}, always=CommandClose)

    with pytest.raises(SiatDown):
        shop.run(run_id="r-1")

    [kept] = shop.runs()
    bill = next(one for one in kept.steps if one.command.endswith("CommandBill"))
    assert (bill.status, bill.kind, bill.retry_after) == (
        StepStatus.FAILED,
        exceptions.FailureKind.UNAVAILABLE,
        30.0,
    )
    assert bill.error == "SIAT is not answering"
    assert (kept.state, kept.error) == (PipelineState.FAILED, "SIAT is not answering")
    assert shop.calls[-1] == "close ok=False failure='SIAT is not answering'"


def test_everything_a_run_executes_says_which_run_it_is():
    done: list[ExecutionCompleted] = []
    shop = Shop("pipe-context")
    shop.bus.on_completion(done.append)

    shop.run(run_id="r-7")

    steps = [one for one in done if one.use_case.endswith(("Collect", "Bill", "Notify"))]
    assert len(steps) == 3
    said = {"pipeline": "pipe-context.CommandRunNightly", "run_id": "r-7"}
    assert all(one.context[ContextKey.PIPELINE_CONTEXT] == said for one in steps)
    assert all(context[ContextKey.PIPELINE_CONTEXT] == said for context in shop.contexts)
    assert "run_id" not in steps[0].context and "pipeline" not in steps[0].context


def test_a_failure_that_escapes_a_pipeline_names_the_step_that_failed():
    heard: list[ExecutionFailed] = []

    def breaks(dto: Any) -> Any:
        raise SiatDown("down")

    shop = Shop("pipe-escapes", answers={CommandNotify: breaks})
    shop.bus.on_failure(heard.append)

    with pytest.raises(SiatDown):
        shop.run(run_id="r-1")

    [failure] = heard
    assert failure.use_case == "pipe-escapes.CommandNotify"
    assert failure.context[ContextKey.PIPELINE_CONTEXT]["run_id"] == "r-1"
    assert (failure.kind, failure.retry_after) == ("unavailable", 30.0)


def test_a_command_outside_a_run_is_recorded_nowhere():
    shop = Shop("pipe-outside")

    shop.bus(CommandCollect(run_id="by-hand"))

    assert shop.runs() == []


def test_a_run_without_a_run_id_gets_one_and_each_run_is_its_own():
    shop = Shop("pipe-anonymous")

    first, second = shop.run(), shop.run()

    assert first.run_id and second.run_id and first.run_id != second.run_id
    assert len(shop.runs()) == 2


# --- the recipe -----------------------------------------------------------------------------------


class Stage(StrEnum):
    REFINE = "refine"
    IMPLEMENT = "implement"


class CommandRunStaged(DataTransferObject):
    run_id: str
    stage: Stage


def test_a_recipe_may_be_picked_by_a_field_of_the_input():
    shop = Shop(
        "pipe-by",
        entry=CommandRunStaged,
        by="stage",
        steps={
            Stage.REFINE: [CommandCollect],
            Stage.IMPLEMENT: [CommandCollect, CommandNotify],
        },
    )

    refine = shop.run(run_id="a", stage=Stage.REFINE)
    implement = shop.run(run_id="b", stage=Stage.IMPLEMENT)

    assert shop.calls == ["CommandCollect", "CommandCollect", "CommandNotify"]
    assert [len(refine.steps), len(implement.steps)] == [1, 2]


def test_a_recipe_the_input_does_not_pick_is_a_programming_error_naming_what_it_has():
    class Many(Pipeline):
        by = "stage"
        steps = {Stage.REFINE: [CommandCollect]}

    with pytest.raises(exceptions.ProgrammingError, match="refine"):
        Many.recipe_of(CommandRunStaged(run_id="a", stage=Stage.IMPLEMENT))
    with pytest.raises(exceptions.ProgrammingError, match="by"):
        type("NoBy", (Pipeline,), {"steps": {"x": []}}).recipe_of(CommandRunNightly())


# --- the same run again ---------------------------------------------------------------------------


def test_by_default_the_same_run_again_starts_over():
    shop = Shop("pipe-restart")
    shop.run(run_id="r-1")

    shop.run(run_id="r-1")

    assert shop.calls == ["CommandCollect", "CommandBill", "CommandNotify"] * 2
    assert len(shop.runs()) == 1


def test_a_run_that_continues_goes_on_after_the_last_step_that_succeeded():
    state = {"credit": False}

    def bills(dto: Any) -> Any:
        return StepAnswer() if state["credit"] else StepAnswer(ok=False, summary="no credit")

    shop = Shop("pipe-continue", answers={CommandBill: bills}, resume=Resume.CONTINUE)
    failed = shop.run(run_id="r-1")
    state["credit"] = True

    again = shop.run(run_id="r-1")

    assert failed.state is PipelineState.FAILED
    assert shop.calls == [
        "CommandCollect",
        "CommandBill",  # failed
        "CommandBill",  # again: collect was done
        "CommandNotify",
    ]
    assert again.state is PipelineState.SUCCEEDED
    assert [one.status for one in again.steps] == [StepStatus.SUCCEEDED] * 3


def test_a_run_kept_under_another_recipe_starts_over_and_says_so(monkeypatch):
    said: list[str] = []
    monkeypatch.setattr(
        "sincpro_framework.pipelines.services.recorder.logger",
        type("L", (), {"warning": said.append})(),
    )
    store = InMemoryContexts()
    first = Shop("pipe-version", steps=[CommandCollect], resume=Resume.CONTINUE, store=store)
    first.run(run_id="r-1")
    second = Shop("pipe-version", resume=Resume.CONTINUE, store=store)

    second.run(run_id="r-1")

    assert second.calls == ["CommandCollect", "CommandBill", "CommandNotify"]
    assert any("another recipe" in message for message in said)


# --- whose a run is: every shape of system --------------------------------------------------------


@pytest.mark.parametrize(
    ("scope", "by", "owner"),
    [
        ({}, Owner.BY_TENANT, "tenant_id=,tenant_ids="),
        (
            {"tenant_id": None, "user_id": None},
            [*Owner.BY_TENANT, "user_id"],
            "tenant_id=,tenant_ids=,user_id=",
        ),
        ({"tenant_id": "acme"}, Owner.BY_TENANT, "tenant_id=acme,tenant_ids="),
        (
            {"user_id": "ana"},
            [*Owner.BY_TENANT, "user_id"],
            "tenant_id=,tenant_ids=,user_id=ana",
        ),
        (
            {"tenant_id": "acme", "user_id": "ana"},
            [*Owner.BY_TENANT, "user_id"],
            "tenant_id=acme,tenant_ids=,user_id=ana",
        ),
        (
            {"tenant_ids": ["globex", "acme"], "user_id": "ana"},
            [*Owner.BY_TENANT, "user_id"],
            "tenant_id=,tenant_ids=acme|globex,user_id=ana",
        ),
    ],
)
def test_a_run_is_kept_for_every_shape_of_system_and_refused_for_none(scope, by, owner):
    shop = Shop(f"pipe-owner-{abs(hash(owner))}", owned_by=tuple(by))

    with shop.bus.context(scope):
        answer = shop.bus(CommandRunNightly(run_id="r-1"))

    assert isinstance(answer, PipelineRun) and answer.state is PipelineState.SUCCEEDED
    [kept] = shop.runs(scope, by=by)
    assert kept.owner == owner


def test_the_runs_of_one_tenant_are_not_the_runs_of_another():
    shop = Shop("pipe-tenants")
    for tenant in ("acme", "globex"):
        with shop.bus.context({"tenant_id": tenant}):
            shop.bus(CommandRunNightly(run_id="r-1"))

    assert [one.owner for one in shop.runs({"tenant_id": "acme"})] == [
        "tenant_id=acme,tenant_ids="
    ]
    assert [one.owner for one in shop.runs({"tenant_id": "globex"})] == [
        "tenant_id=globex,tenant_ids="
    ]
    assert shop.runs({}) == []


def test_with_no_context_store_the_runs_are_kept_in_the_memory_of_the_process():
    shop = Shop("pipe-memory")
    shop.bus.store_context_in(InMemoryContexts())  # replaced below: no store of its own
    bare = UseFramework("pipe-bare", log_after_execution=False)

    @bare.feature(CommandCollect)
    class Collect(Feature):
        def execute(self, dto: CommandCollect) -> None:
            return None

    class RunBare(Pipeline, ApplicationService):
        steps = [CommandCollect]

    bare.app_service(CommandRunNightly)(RunBare)

    with bare.context({"tenant_id": "memory-only"}):
        answer = bare(CommandRunNightly(run_id="r-1"))
        assert isinstance(answer, PipelineRun)
        found = PipelineRuns.of().find("pipe-bare.CommandRunNightly")

    assert answer.state is PipelineState.SUCCEEDED
    assert [one.run_id for one in found] == ["r-1"]


# --- what is left to the project ------------------------------------------------------------------


def test_a_pipeline_that_does_not_record_is_only_the_order_of_its_steps():
    shop = Shop("pipe-silent", records=False)

    answer = shop.run(run_id="r-1")

    assert shop.calls == ["CommandCollect", "CommandBill", "CommandNotify"]
    assert answer.state is PipelineState.SUCCEEDED
    assert shop.runs() == []


def test_a_step_the_bus_does_not_answer_is_said_when_the_bus_is_built_and_refused_nowhere(
    monkeypatch,
):
    said: list[str] = []
    monkeypatch.setattr(recorder_module, "logger", type("L", (), {"warning": said.append})())

    class CommandUnknown(DataTransferObject):
        needs: str

    shop = Shop("pipe-warns", steps=[CommandCollect, CommandUnknown])

    shop.bus.build_root_bus()

    assert any("CommandUnknown is not answered" in message for message in said)
    assert any("needs 'needs'" in message for message in said)


def test_a_generation_of_the_bus_keeps_its_runs_too():
    shop = Shop("pipe-fresh")

    generation = shop.bus.fresh()
    generation(CommandRunNightly(run_id="r-1"))

    with generation.context({}):
        found = PipelineRuns.of().find("pipe-fresh.CommandRunNightly")
    assert [one.run_id for one in found] == ["r-1"]


def test_a_step_that_is_not_a_data_transfer_object_is_a_programming_error():
    class Plain:
        pass

    class Odd(Pipeline):
        steps = [Plain]

    with pytest.raises(exceptions.ProgrammingError, match="not a DataTransferObject"):
        Odd().command_of(Plain, CommandRunNightly())


def test_the_pipeline_module_is_what_a_project_imports():
    assert pipeline_module.Pipeline is Pipeline


# --- many at once ---------------------------------------------------------------------------------


def test_runs_at_the_same_time_each_see_only_their_own_run():
    """N runs at once are N branches of the context: a step reads the run it belongs to, and
    each run is its own entry of the store."""
    import threading

    from sincpro_framework.context import ContextThread

    seen: list[tuple[str, str]] = []
    lock = threading.Lock()
    bus = UseFramework("pipe-many", log_after_execution=False)
    bus.store_context_in(InMemoryContexts())

    @bus.feature(CommandCollect)
    class Collect(Feature):
        def execute(self, dto: CommandCollect) -> None:
            with lock:
                seen.append(
                    (dto.run_id, use_context()[ContextKey.PIPELINE_CONTEXT]["run_id"])
                )

    class RunMany(Pipeline, ApplicationService):
        steps = [CommandCollect, CommandCollect, CommandCollect]

    bus.app_service(CommandRunNightly)(RunMany)
    start = threading.Barrier(8)

    def run(number: int) -> None:
        start.wait()
        bus(CommandRunNightly(run_id=f"r-{number}"))

    threads = [ContextThread(target=run, args=(number,)) for number in range(8)]
    for one in threads:
        one.start()
    for one in threads:
        one.join()

    assert len(seen) == 24
    assert all(handed == said for handed, said in seen)
    with bus.context({}):
        kept = PipelineRuns.of().find("pipe-many.CommandRunNightly")
    assert sorted(one.run_id for one in kept) == [f"r-{number}" for number in range(8)]
    assert all(one.state is PipelineState.SUCCEEDED for one in kept)


def test_a_second_writer_of_the_same_run_makes_the_run_fail_instead_of_overwriting_it():
    """Two replicas driving one run: the one whose version moved under it loses, out loud."""

    def another_replica(dto: Any) -> Any:
        runs = PipelineRuns.of()
        current = runs.read("pipe-two.CommandRunNightly", "r-1")
        assert current is not None
        runs.save(current, current.store_version)
        return StepAnswer()

    shop = Shop("pipe-two", answers={CommandBill: another_replica})

    with pytest.raises(context_exceptions.EntryConflict):
        shop.run(run_id="r-1")
