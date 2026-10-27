"""`Pipeline`: an ApplicationService that runs a recipe of steps, and saves how far it got.

    @billing.app_service(CommandRunNightly)
    class RunNightly(Pipeline, ApplicationService):
        steps = [CommandCollect, CommandBill, CommandNotify]
        always = CommandClose                          runs whether the steps succeeded or not

    billing(CommandRunNightly(run_id="2026-10-23"))   the steps, in order; the run saved in the context

A recipe is data: Commands in the order they run, each a Feature of the same bus. Each Command is
filled by the names of its fields — from the input (`run_id`, `stage`, `timeout_seconds`) and from
what the steps before it answered, the latest first: the `invoice_id` step 1 creates is the
`invoice_id` of step 2, also when the run goes on in another request. A step that answers
`ok=False` ends the run as failed, and one that raises ends it with the exception, after `always`.

**One recipe or several.** A mapping picks the recipe by a field of the input:

    class RunIssue(Pipeline, ApplicationService):
        by = "stage"
        steps = {
            Stage.REFINE: [*COMMON, CommandSaveSpec],
            Stage.IMPLEMENT: [*COMMON, CommandVerify, CommandDeliver],
        }

**Where the run is stored is the context's** (`PipelineRuns.of()`), beside the drafts — in the
process's memory, a Redis, or a store of the project's own. With no context store it is stored in the
process's memory, and said once in the logs. Whose it is comes from the context — no tenant and no
user, a tenant, a user, a user across tenants — by the keys `owned_by` names, the tenant's by
default. The same `run_id`, run again, is the same run: it starts over, or with
`resume = Resume.CONTINUE` goes on after the last step that succeeded.

**Retrying a step** is said once, for every step: `retry = Retry()` runs again a step whose failure
is worth running again as it is — a service unavailable, a conflict, a claim in progress, as every
wire classifies it — waiting what the error says (`retry_after`), longer each time. A step that
answers `ok=False`, or a rule of the business that refused it, is never retried.

**Waiting for a decision** is a step of the recipe: `Await("approval")` stops the run there, as
`WAITING`, without running `always`. The same run, run again with the input's `approval` field
filled, goes on after it — with the input it was first given, never this call's — and every later
step that has a field `approval` is given it:

    steps = [CommandRefine, Await("approval"), CommandImplement]
    billing(CommandRunIssue(run_id="I-7"))                       →  WAITING for approval
    billing(CommandRunIssue(run_id="I-7", approval="approved"))  →  refine is not run again

Only a run that waited takes a decision: a first call that carries one waits all the same. The
field defaults to `None` or `""` — `confirmed: bool = False` would pass every run undecided, and is
a `ProgrammingError` when the bus is built. A single Command a person approves, with no sequence
around it, is an approval (`sincpro_framework.approvals`).

Everything here is a default a project may leave. The recorder that writes the run is an interceptor
the bus attaches; `records = False` leaves it out, and the pipeline is then only the order of its
steps. Nothing in the framework refuses a recipe for what it asks — a step the bus does not answer,
or one that needs a field the input lacks, is said in the logs when the bus is built.
"""

import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sincpro_framework.common.failures import refined_failure_kind, retry_after
from sincpro_framework.common.naming import registered_name
from sincpro_framework.context import context_executions, use_context
from sincpro_framework.pipelines.domain import exceptions
from sincpro_framework.pipelines.domain.recipe import UNDECIDED, Await, PipelineDeclaration
from sincpro_framework.pipelines.domain.run import (
    PipelineRun,
    PipelineState,
    RunInProgress,
    StepStatus,
)
from sincpro_framework.sincpro_logger import logger


class Pipeline(PipelineDeclaration):
    """The order of the steps of an ApplicationService, declared as data — and the `execute`
    that runs them.

    Write `class RunNightly(Pipeline, ApplicationService)` — the pipeline first, so its `execute`
    is the one that runs — and register it with `@bus.app_service(CommandRunNightly)` as any other.
    What it declares (`steps`, `by`, `always`, `resume`, `owned_by`, `records`, `retry`, `lease`)
    is `PipelineDeclaration`'s.
    """

    if TYPE_CHECKING:
        feature_bus: Any

    def command_of(self, step: type, dto: Any, **extras: Any) -> Any:
        """`step` filled with the fields of `dto` — and of `extras`, first: what the steps
        before it answered — that carry its names."""
        fields = getattr(step, "model_fields", None)
        if fields is None:
            raise exceptions.ProgrammingError(
                f"{step.__name__}, a step of {type(self).__name__}, is not a DataTransferObject: "
                "a step is filled by the names of its fields"
            )
        return step(
            **{
                name: extras[name] if name in extras else getattr(dto, name)
                for name in fields
                if name in extras or hasattr(dto, name)
            }
        )

    def answer(self, dto: Any, failure: str) -> Any:
        """What the call answers: the run as it stands, with how it ended — or that it waits. A
        project that wants another answer overrides this.

            answer(command, "")                   ->  PipelineRun(state=SUCCEEDED, error="")
            answer(command, "Charge failed")      ->  PipelineRun(state=FAILED,
                                                                  error="Charge failed")
            answer(command, "")  waiting for "approval"
                                  ->  PipelineRun(state=WAITING, waiting_for="approval")

        The run saved for this execution when there is one; else a new one named after the
        Command, its `run_id` and the moment now."""
        holder = use_context().get(RunInProgress)
        if holder is not None:
            run = holder.run
        else:
            execution = context_executions.current()
            run = PipelineRun(
                pipeline=registered_name(type(dto), execution.bus if execution else ""),
                run_id=str(getattr(dto, "run_id", "")),
                started_at=datetime.now(UTC),
            )
        waiting = holder.waiting_for if holder is not None else None
        state = (
            PipelineState.WAITING
            if waiting
            else PipelineState.FAILED if failure else PipelineState.SUCCEEDED
        )
        return run.model_copy(
            update={"state": state, "error": failure, "waiting_for": waiting}
        )

    def _executed(self, step: type, dto: Any, handed: dict[str, Any]) -> Any:
        """`step`, filled from `dto` and what the steps before it answered (`handed`), run on
        the feature bus — again while its failure is one `retry` runs again, after the wait it
        asks."""
        attempt = 1
        while True:
            try:
                return self.feature_bus.execute(self.command_of(step, dto, **handed))
            except Exception as error:
                kind = refined_failure_kind(error)
                if (
                    self.retry is None
                    or attempt >= self.retry.attempts
                    or kind not in self.retry.on
                ):
                    raise
                time.sleep(
                    self.retry.wait_before(attempt, retry_after(error, kind)).total_seconds()
                )
                attempt += 1

    def _decided(self, waits: Await, dto: Any, holder: RunInProgress | None) -> bool:
        """Whether the run goes on past `waits`: it waited here, and this call's input fills
        its field. Otherwise the run waits there — which only a run that is saved can do — even
        when a first call already carries the decision: nothing passes an `Await` without
        having waited at it."""
        if not hasattr(dto, waits.name):
            raise exceptions.ProgrammingError(
                f"{type(self).__name__} waits for '{waits.name}', and {type(dto).__name__} has no "
                f"field '{waits.name}' to be run again with"
            )
        if holder is None:
            raise exceptions.ProgrammingError(
                f"{type(self).__name__} waits for '{waits.name}' and saves no run to wait in: "
                "a pipeline that awaits records its runs (records = True)"
            )
        decision = getattr(dto, waits.name)
        if holder.resumed_from == waits.name and decision not in UNDECIDED:
            holder.decided[waits.name] = decision
            return True
        if decision not in UNDECIDED:
            logger.warning(
                f"pipeline run {holder.run.run_id} of {holder.run.pipeline} was not waiting "
                f"for '{waits.name}': the decision this call carries is not taken, the run "
                "waits for it first"
            )
        holder.waiting_for = waits.name
        return False

    def execute(self, dto: Any) -> Any:
        """Run the recipe of `dto`, step by step, and answer how the run ended.

            pipeline.execute(CommandCheckout(method="card", ...))
                ->  Reserve, Charge, then `always` — PipelineRun(state=SUCCEEDED)
            ... Charge answers ok=False, summary="card declined"
                ->  Reserve, Charge, `always` with ok=False — PipelineRun(state=FAILED,
                    error="card declined")

        1. The steps of the recipe, in order; the ones an earlier run of this `run_id` already
           did are left, when it continues.
        2. Each step is filled from the input and from what the steps before it answered — the
           latest answer first, also those of a run continued in another request — and run on
           the feature bus — again, while `retry` says so; one that answers `ok=False` ends the
           run as failed.
        3. An `Await` stops the run there, waiting — unless the run waited at it and this
           call's input fills its field.
        4. The `always` Command last, whatever happened — given `ok` and `failure` — unless
           the run waits (it has not ended) or its lease was lost (it is no longer this
           execution's).
        Final: `answer(dto, failure)` — the run as it stands, by default."""
        holder = use_context().get(RunInProgress)
        done = {
            index
            for index, step in enumerate(holder.run.steps if holder is not None else [])
            if step.status is StepStatus.SUCCEEDED
        }
        handed: dict[str, Any] = {}
        for index in sorted(done):
            handed.update(holder.run.steps[index].answer if holder is not None else {})
        failure = ""
        waiting = False
        try:
            for index, step in enumerate(self.recipe_of(dto)):
                if index in done:
                    continue
                if isinstance(step, Await):
                    if self._decided(step, dto, holder):
                        continue
                    waiting = True
                    break
                response = self._executed(step, dto, handed)
                if hasattr(response, "model_dump"):
                    handed.update(response.model_dump())
                if getattr(response, "ok", True) is False:
                    failure = getattr(response, "summary", "") or f"{step.__name__} failed"
                    break
        except Exception as error:
            failure = str(error) or type(error).__name__
            raise
        finally:
            lost = (
                holder is not None and holder.lease is not None and holder.lease.lost.is_set()
            )
            if self.always is not None and not waiting and not lost:
                self.feature_bus.execute(
                    self.command_of(
                        self.always,
                        dto,
                        **{**handed, "ok": failure == "", "failure": failure},
                    )
                )
        return self.answer(dto, failure)
