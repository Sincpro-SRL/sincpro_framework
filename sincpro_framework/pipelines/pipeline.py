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

Everything here is a default a project may leave. The recorder that writes the run is an interceptor
the bus attaches; `records = False` leaves it out, and the pipeline is then only the order of its
steps. Nothing in the framework refuses a recipe for what it asks — a step the bus does not answer,
or one that needs a field the input lacks, is said in the logs when the bus is built.
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, ClassVar

from sincpro_framework import exceptions
from sincpro_framework.common.naming import registered_name
from sincpro_framework.context.entrypoint.facade import use_context
from sincpro_framework.context.infrastructure.tree import current_execution
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


class Pipeline:
    """The order of the steps of an ApplicationService, declared as data.

    Write `class RunNightly(Pipeline, ApplicationService)` — the pipeline first, so its `execute`
    is the one that runs — and register it with `@bus.app_service(CommandRunNightly)` as any other.
    """

    steps: ClassVar[Sequence[type] | Mapping[Any, Sequence[type]]] = ()
    """The Commands it runs, in order: a list, or a mapping of recipes picked by `by`."""
    by: ClassVar[str | None] = None
    """The field of the input that picks the recipe, when `steps` is a mapping."""
    always: ClassVar[type | None] = None
    """A Command that runs last whatever happened. It may take `ok` and `failure`."""
    resume: ClassVar[Resume] = Resume.RESTART
    owned_by: ClassVar[Sequence[str]] = ()
    """The keys of the context a run belongs to, beside the tenant: `("user_id",)`."""
    records: ClassVar[bool] = True
    """Whether the bus attaches the recorder that keeps the run."""

    if TYPE_CHECKING:
        feature_bus: Any

    @classmethod
    def recipes(cls) -> list[Sequence[type]]:
        """Every recipe it has, in the order declared."""
        if isinstance(cls.steps, Mapping):
            return list(cls.steps.values())
        return [cls.steps]

    @classmethod
    def recipe_of(cls, dto: Any) -> list[type]:
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
        """What the call answers: the run as it stands, with how it ended. A project that wants
        another answer overrides this."""
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
        return run.model_copy(
            update={
                "state": PipelineState.FAILED if failure else PipelineState.SUCCEEDED,
                "error": failure,
            }
        )

    def execute(self, dto: Any) -> Any:
        """1. The steps of the recipe, in order; the ones an earlier run of this `run_id` already
           did are left, when it continues.
        2. Each step is filled from the input and run on the feature bus; one that answers
           `ok=False` ends the run as failed.
        3. The `always` Command last, whatever happened, with `ok` and `failure`.
        Final: the answer."""
        holder = use_context().get(RunInProgress)
        done = {
            index
            for index, step in enumerate(holder.run.steps if holder is not None else [])
            if step.status is StepStatus.SUCCEEDED
        }
        failure = ""
        try:
            for index, step in enumerate(self.recipe_of(dto)):
                if index in done:
                    continue
                response = self.feature_bus.execute(self.command_of(step, dto))
                if getattr(response, "ok", True) is False:
                    failure = getattr(response, "summary", "") or f"{step.__name__} failed"
                    break
        except Exception as error:
            failure = str(error) or type(error).__name__
            raise
        finally:
            if self.always is not None:
                self.feature_bus.execute(
                    self.command_of(self.always, dto, ok=failure == "", failure=failure)
                )
        return self.answer(dto, failure)
