"""`Pipeline`: an ApplicationService that runs a recipe of steps, and keeps how far it got.

    @billing.app_service(CommandRunNightly)
    class RunNightly(Pipeline, ApplicationService):
        steps = [CommandCollect, CommandBill, CommandNotify]
        always = CommandClose                          runs whether the steps succeeded or not

    billing(CommandRunNightly(run_id="2026-10-23"))   the steps, in order; the run kept in the context

A recipe is data: Commands in the order they run, each a Feature of the same bus. Each Command is
filled with the fields of the input that carry its names — `run_id`, `stage`, `timeout_seconds` —
so a step needs nothing from the one before it: what a step hands the next is kept where the
project keeps its data. A step that answers `ok=False` ends the run as failed, and one that raises
ends it with the exception, after `always`.

**One recipe or several.** A mapping picks the recipe by a field of the input:

    class RunIssue(Pipeline, ApplicationService):
        by = "stage"
        steps = {
            Stage.REFINE: [*COMMON, CommandKeepSpec],
            Stage.IMPLEMENT: [*COMMON, CommandVerify, CommandDeliver],
        }

**Where the run is kept is the context's** (`use_context().pipelines()`), beside the drafts — in the
process's memory, a Redis, or a store of the project's own. With no context store it is kept in the
process's memory, and said once in the logs. Whose it is comes from the context — no tenant and no
user, a tenant, a user, a user across tenants — and `owned_by` adds the keys a project says a run
belongs to. The same `run_id`, run again, is the same run: it starts over, or with
`resume = Resume.CONTINUE` goes on after the last step that succeeded.

**Retrying a step** is said once, for every step: `retry = Retry()` runs again a step whose failure
is worth running again as it is — a service unavailable, a conflict, a claim in progress, as every
wire classifies it — waiting what the error says (`retry_after`), longer each time. A step that
answers `ok=False`, or a rule of the business that refused it, is never retried.

**Waiting for a decision** is a step of the recipe: `Await("approval")` stops the run there, as
`WAITING`, without running `always`. The same run, run again with the input's `approval` field
filled, goes on after it — and every later step that has a field `approval` is given it:

    steps = [CommandRefine, Await("approval"), CommandImplement]
    billing(CommandRunIssue(run_id="I-7"))                       →  WAITING for approval
    billing(CommandRunIssue(run_id="I-7", approval="approved"))  →  refine is not run again

Everything here is a default a project may leave. The recorder that writes the run is an interceptor
the bus attaches; `records = False` leaves it out, and the pipeline is then only the order of its
steps. Nothing in the framework refuses a recipe for what it asks — a step the bus does not answer,
or one that needs a field the input lacks, is said in the logs when the bus is built.
"""

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any, ClassVar

from sincpro_framework.common.failures import RETRYABLE, refined_failure_kind, retry_after
from sincpro_framework.common.naming import registered_name
from sincpro_framework.context.entrypoint.facade import use_context
from sincpro_framework.context.infrastructure.tree import current_execution
from sincpro_framework.pipelines import exceptions
from sincpro_framework.pipelines.run import (
    PipelineRun,
    PipelineState,
    RunInProgress,
    StepStatus,
)


class Resume(StrEnum):
    """What a run does when the same `run_id` is run again."""

    RESTART = "restart"
    """Every step again, from the first."""
    CONTINUE = "continue"
    """From the first step that did not succeed — when the recipe is the one it ran."""


@dataclass(frozen=True)
class Await:
    """A step that waits: the run stops here until it is run again with the input's field of
    this name filled — what was decided, by a person or another system."""

    name: str

    @property
    def step(self) -> str:
        """How the run names it among its steps."""
        return f"await:{self.name}"


@dataclass(frozen=True)
class Retry:
    """How a pipeline runs a step again after it failed in a way worth running again as it is.

    Retry()                                           3 attempts, as the error says to wait
    Retry(attempts=5, max_wait=timedelta(minutes=1))  more, and longer
    """

    attempts: int = 3
    """How many times a step is run at most, the first included."""
    backoff: float = 2.0
    """Each wait is this many times the one before."""
    max_wait: timedelta = timedelta(seconds=30)
    """No wait is longer: a step that needs more is a run to continue later."""
    on: frozenset[exceptions.FailureKind] = RETRYABLE | {exceptions.FailureKind.CONFLICT}
    """The kinds of failure retried — unavailable, in progress, exhausted, a conflict."""

    def wait_before(self, attempt: int, declared: timedelta | None) -> timedelta:
        """The wait before `attempt` + 1: what the error declared — a second when it says
        nothing — grown by `backoff` each attempt, never above `max_wait`."""
        first = declared if declared is not None else timedelta(seconds=1)
        return min(first * self.backoff ** (attempt - 1), self.max_wait)


class Pipeline:
    """The order of the steps of an ApplicationService, declared as data.

    Write `class RunNightly(Pipeline, ApplicationService)` — the pipeline first, so its `execute`
    is the one that runs — and register it with `@bus.app_service(CommandRunNightly)` as any other.
    """

    steps: ClassVar[Sequence[type | Await] | Mapping[Any, Sequence[type | Await]]] = ()
    """The Commands it runs, in order — and the decisions it waits for (`Await`): a list, or a
    mapping of recipes picked by `by`."""
    by: ClassVar[str | None] = None
    """The field of the input that picks the recipe, when `steps` is a mapping."""
    always: ClassVar[type | None] = None
    """A Command that runs last whatever happened. It may take `ok` and `failure`."""
    resume: ClassVar[Resume] = Resume.RESTART
    owned_by: ClassVar[Sequence[str]] = ()
    """The keys of the context a run belongs to, beside the tenant: `("user_id",)`."""
    records: ClassVar[bool] = True
    """Whether the bus attaches the recorder that keeps the run."""
    retry: ClassVar[Retry | None] = None
    """How a step that failed in a way worth running again is run again — never, by default."""

    if TYPE_CHECKING:
        feature_bus: Any

    @classmethod
    def recipes(cls) -> list[Sequence[type | Await]]:
        """Every recipe it has, in the order declared."""
        if isinstance(cls.steps, Mapping):
            return list(cls.steps.values())
        return [cls.steps]

    @classmethod
    def recipe_of(cls, dto: Any) -> list[type | Await]:
        """The steps `dto` runs, in order — the list, or the recipe its `by` field picks."""
        if not isinstance(cls.steps, Mapping):
            return list(cls.steps)
        if cls.by is None:
            raise exceptions.ProgrammingError(
                f"{cls.__name__} has a recipe per value and says nothing of which field picks it: "
                'declare `by = "<field of the input>"`'
            )
        picked = getattr(dto, cls.by)
        if picked not in cls.steps:
            raise exceptions.ProgrammingError(
                f"{cls.__name__} has no recipe for {cls.by}={picked!r}: it has "
                f"{', '.join(repr(one) for one in cls.steps)}"
            )
        return list(cls.steps[picked])

    def command_of(self, step: type, dto: Any, **extras: Any) -> Any:
        """`step` filled with the fields of `dto` — and of `extras` — that carry its names."""
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
        project that wants another answer overrides this."""
        holder = use_context().get(RunInProgress)
        if holder is not None:
            run = holder.run
        else:
            execution = current_execution()
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

    def _executed(self, step: type, dto: Any) -> Any:
        """`step` run on the feature bus — again while its failure is one `retry` runs again,
        after the wait it asks."""
        attempt = 1
        while True:
            try:
                return self.feature_bus.execute(self.command_of(step, dto))
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
        """Whether the run goes on past `waits`: its field of the input is filled. When it is
        not, the run waits there — which only a run that is kept can do."""
        if not hasattr(dto, waits.name):
            raise exceptions.ProgrammingError(
                f"{type(self).__name__} waits for '{waits.name}', and {type(dto).__name__} has no "
                f"field '{waits.name}' to be run again with"
            )
        if getattr(dto, waits.name) not in (None, ""):
            return True
        if holder is None:
            raise exceptions.ProgrammingError(
                f"{type(self).__name__} waits for '{waits.name}' and keeps no run to wait in: "
                "a pipeline that awaits records its runs (records = True)"
            )
        holder.waiting_for = waits.name
        return False

    def execute(self, dto: Any) -> Any:
        """1. The steps of the recipe, in order; the ones an earlier run of this `run_id` already
           did are left, when it continues.
        2. Each step is filled from the input and run on the feature bus — again, while `retry`
           says so; one that answers `ok=False` ends the run as failed.
        3. An `Await` whose field the input leaves empty stops the run there, waiting.
        4. The `always` Command last, whatever happened — unless the run waits: it has not ended.
        Final: the answer."""
        holder = use_context().get(RunInProgress)
        done = {
            index
            for index, step in enumerate(holder.run.steps if holder is not None else [])
            if step.status is StepStatus.SUCCEEDED
        }
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
                response = self._executed(step, dto)
                if getattr(response, "ok", True) is False:
                    failure = getattr(response, "summary", "") or f"{step.__name__} failed"
                    break
        except Exception as error:
            failure = str(error) or type(error).__name__
            raise
        finally:
            if self.always is not None and not waiting:
                self.feature_bus.execute(
                    self.command_of(self.always, dto, ok=failure == "", failure=failure)
                )
        return self.answer(dto, failure)
