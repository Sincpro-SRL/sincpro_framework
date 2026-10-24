"""`PipelineRecorder`: the interceptor a `Pipeline` brings, that keeps how far each run got.

It wraps the pipeline's own Command and every step's, and it is the only one that writes the run:
the pipeline itself is only the order of its steps. Around the pipeline it opens the run — new, or
continued — and closes it; around each step it marks it running and then how it ended. It sees the
exception of a step the pipeline catches, which an event announced when a call escapes does not.

The run it drives is kept on the node of the pipeline's execution (`RunInProgress`), so each change
is one write to the store and no read: a step on one of a hundred replicas costs what it costs on
one. Every write names the version it read, so a second writer of the same run loses with
`EntryConflict` instead of overwriting it in silence.

Context: it is inert for a step run outside a pipeline (a script calling the Command), and for a step
of another pipeline's run — it acts only where `SINCPRO_PIPELINE_CONTEXT` names this pipeline.
"""

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sincpro_framework.common.failures import refined_failure_kind, retry_after
from sincpro_framework.common.ids import new_entity_id
from sincpro_framework.common.naming import registered_name
from sincpro_framework.context.domain.keys import ContextKey
from sincpro_framework.context.entrypoint.facade import use_context
from sincpro_framework.context.infrastructure.tree import current_execution
from sincpro_framework.pipelines.pipeline import Await, Resume
from sincpro_framework.pipelines.run import (
    PipelineRun,
    PipelineState,
    RunInProgress,
    StepState,
    StepStatus,
)
from sincpro_framework.pipelines.runs import PipelineRuns
from sincpro_framework.sincpro_logger import logger
from sincpro_framework.store import Lease

if TYPE_CHECKING:
    from sincpro_framework.pipelines.pipeline import Pipeline


class PipelineRecorder:
    """The interceptor of one pipeline: its Command and its steps."""

    def __init__(self, pipeline: "type[Pipeline]", command: type) -> None:
        """The recorder of `pipeline`, registered for its `command` and its steps."""
        self.pipeline = pipeline
        self.command = command

    def _runs(self) -> PipelineRuns:
        """The runs of whoever the context of the call says, by the pipeline's `owned_by`."""
        return use_context().pipelines(by=self.pipeline.owned_by)

    def _keep(self, holder: RunInProgress, changed: dict[str, Any]) -> PipelineRun:
        """The run as it is now, written over the version this execution last kept."""
        holder.run = self._runs().save(
            holder.run.model_copy(update=changed), holder.run.store_version
        )
        return holder.run

    def _decisions(self, dto: Any, holder: RunInProgress) -> tuple[dict[str, Any], list[Any]]:
        """What the input decided for each `Await` of its recipe, and the steps with each await
        marked: passed when decided, waiting where the run stopped."""
        decided = dict(holder.run.decisions)
        for waits in self.pipeline.recipe_of(dto):
            if isinstance(waits, Await) and getattr(dto, waits.name, None) not in (None, ""):
                decided[waits.name] = getattr(dto, waits.name)
        steps = [one.model_copy() for one in holder.run.steps]
        for one in steps:
            name = one.command.removeprefix("await:")
            if not one.command.startswith("await:"):
                continue
            if name in decided:
                one.status = StepStatus.SUCCEEDED
            elif name == holder.waiting_for:
                one.status = StepStatus.WAITING
        return decided, steps

    def _close(self, dto: Any, holder: RunInProgress, error: str) -> PipelineRun:
        """The run finished — failed by what escaped, or by a step that failed; else succeeded —
        or stopped, waiting at an `Await`."""
        decided, steps = self._decisions(dto, holder)
        if holder.waiting_for and not error:
            return self._keep(
                holder,
                {
                    "state": PipelineState.WAITING,
                    "waiting_for": holder.waiting_for,
                    "decisions": decided,
                    "steps": steps,
                    "current": None,
                },
            )
        failed = next((step for step in steps if step.status is StepStatus.FAILED), None)
        why = error or (
            failed.error or f"{failed.command} failed" if failed is not None else ""
        )
        return self._keep(
            holder,
            {
                "state": PipelineState.FAILED if why else PipelineState.SUCCEEDED,
                "waiting_for": None,
                "decisions": decided,
                "steps": steps,
                "current": None,
                "error": why,
                "finished_at": datetime.now(UTC),
            },
        )

    def _opened(self, dto: Any, name: str, bus: str, run_id: str) -> PipelineRun:
        """The run this execution drives, opened and kept.

        1. The recipe this input runs, and its version — a hash of its steps' names, `always`
           included.
        2. The run: continued when the pipeline says so and it ran this recipe, else new.
        Final: kept over what was there."""
        recipe = [*self.pipeline.recipe_of(dto)]
        if self.pipeline.always is not None:
            recipe.append(self.pipeline.always)
        names = [
            step.step if isinstance(step, Await) else registered_name(step, bus)
            for step in recipe
        ]
        version = hashlib.sha256("|".join(names).encode()).hexdigest()[:12]
        runs = self._runs()
        before = runs.read(name, run_id)
        continuing = before is not None and (
            self.pipeline.resume is Resume.CONTINUE or before.state is PipelineState.WAITING
        )
        if continuing and before is not None and before.version != version:
            logger.warning(
                f"pipeline run {run_id} of {name} was kept under another recipe: it starts over"
            )
            continuing = False
        execution = current_execution()
        correlation = execution.correlation_id if execution is not None else None
        if continuing and before is not None:
            run = before.model_copy(
                update={
                    "state": PipelineState.RUNNING,
                    "current": None,
                    "waiting_for": None,
                    "error": "",
                    "finished_at": None,
                    "correlation_id": correlation,
                    "steps": [
                        (
                            step
                            if step.status is StepStatus.SUCCEEDED
                            else StepState(command=step.command)
                        )
                        for step in before.steps
                    ],
                }
            )
        else:
            run = PipelineRun(
                pipeline=name,
                run_id=run_id,
                version=version,
                input_name=registered_name(type(dto), bus),
                input=dto.model_dump(mode="json") if hasattr(dto, "model_dump") else {},
                steps=[StepState(command=one) for one in names],
                correlation_id=correlation,
                started_at=datetime.now(UTC),
            )
        return runs.save(run, 0 if before is None else before.store_version)

    def _driven(
        self,
        dto: Any,
        call_next: Callable[[Any], Any],
        name: str,
        bus: str,
        run_id: str,
        lease: Lease | None,
    ) -> Any:
        """The run opened, the pipeline run inside it, and the run closed.

        1. The run opened, and the context of this execution says which run it is.
        Final: the run closed — failed with the exception that escaped, or by its steps — and
        what the caller gets is the run as kept. A run whose lease was lost is not closed: it
        is no longer this execution's to write."""
        holder = RunInProgress(self._opened(dto, name, bus, run_id), lease=lease)
        context = use_context()
        context.set(RunInProgress, holder)
        context.set(
            ContextKey.PIPELINE_CONTEXT, {"pipeline": name, "run_id": holder.run.run_id}
        )
        try:
            response = call_next(dto)
        except Exception as error:
            if lease is None or not lease.lost.is_set():
                self._close(dto, holder, str(error) or type(error).__name__)
            raise
        if lease is not None:
            lease.check()
        closed = self._close(dto, holder, "")
        return closed if isinstance(response, PipelineRun) else response

    def _run(self, dto: Any, call_next: Callable[[Any], Any], name: str, bus: str) -> Any:
        """The run driven — under its lease when the pipeline holds one: taken before the run is
        touched, so a second execution of the same run is refused (`LeaseTaken`) before it writes
        or runs a step, and renewed while its steps run."""
        run_id = str(getattr(dto, "run_id", "") or new_entity_id())
        if self.pipeline.lease is None:
            return self._driven(dto, call_next, name, bus, run_id, None)
        execution = current_execution()
        with use_context().leases().held(
            self._runs().lease_name(name, run_id),
            owner=execution.execution_id if execution is not None else "",
            ttl=self.pipeline.lease,
        ) as lease:
            return self._driven(dto, call_next, name, bus, run_id, lease)

    def _mark(
        self,
        holder: RunInProgress,
        step: str,
        status: StepStatus,
        error: str = "",
        kind: Any = None,
        wait: float | None = None,
    ) -> None:
        """The first step of that name that is waiting (to start) or running (to end) takes
        `status` — to start again, the one that just failed, when the pipeline retries it; one
        the recipe never named is added."""
        waiting = StepStatus.PENDING if status is StepStatus.RUNNING else StepStatus.RUNNING
        now = datetime.now(UTC)
        steps = [one.model_copy() for one in holder.run.steps]
        found = (
            next(
                (
                    one
                    for one in reversed(steps)
                    if one.command == step and one.status is StepStatus.FAILED
                ),
                None,
            )
            if status is StepStatus.RUNNING and holder.run.current == step
            else None
        )
        if found is None:
            found = next(
                (one for one in steps if one.command == step and one.status is waiting), None
            )
        if found is None:
            found = StepState(command=step)
            steps.append(found)
        if status is StepStatus.RUNNING:
            found.started_at = now
            found.attempts += 1
        else:
            found.finished_at = now
        found.status, found.error, found.kind, found.retry_after = status, error, kind, wait
        current = step if status is StepStatus.RUNNING else holder.run.current
        self._keep(holder, {"steps": steps, "current": current})

    def _step(
        self, dto: Any, call_next: Callable[[Any], Any], holder: RunInProgress, bus: str
    ) -> Any:
        """The step marked running; then succeeded, or failed — by what it answered (`ok=False`)
        or by the exception it raised, with its kind and when to try again."""
        if holder.lease is not None:
            holder.lease.check()
        step = registered_name(type(dto), bus)
        self._mark(holder, step, StepStatus.RUNNING)
        try:
            response = call_next(dto)
        except Exception as error:
            kind = refined_failure_kind(error)
            wait = retry_after(error, kind)
            self._mark(
                holder,
                step,
                StepStatus.FAILED,
                error=str(error) or type(error).__name__,
                kind=kind,
                wait=wait.total_seconds() if wait is not None else None,
            )
            raise
        if getattr(response, "ok", True) is False:
            summary = getattr(response, "summary", "") or f"{step} failed"
            self._mark(holder, step, StepStatus.FAILED, error=summary)
        else:
            self._mark(holder, step, StepStatus.SUCCEEDED)
        return response

    def __call__(self, dto: Any, call_next: Callable[[Any], Any]) -> Any:
        """The interceptor: drive a run around the pipeline's Command, mark a step around a
        step of this pipeline's run, and stay out of everything else.

        1. The pipeline's own Command: the run is opened, driven and closed (`_run`).
        2. A step whose execution stands in a run of this pipeline —
           `SINCPRO_PIPELINE_CONTEXT` names it and the run is on the node — is marked
           (`_step`).
        Final: anything else — a step called by a script, or by another pipeline's run — runs
        untouched.
        """
        execution = current_execution()
        bus = execution.bus if execution is not None else ""
        name = registered_name(self.command, bus)
        if type(dto) is self.command:
            return self._run(dto, call_next, name, bus)
        context = use_context()
        said = context.get(ContextKey.PIPELINE_CONTEXT) or {}
        holder = context.get(RunInProgress)
        if holder is not None and said.get("pipeline") == name:
            return self._step(dto, call_next, holder, bus)
        return call_next(dto)
