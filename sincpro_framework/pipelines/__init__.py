"""Pipelines: an ApplicationService that is the order of its steps, and saves how far each run got.

    from sincpro_framework.pipelines import Pipeline, Resume

    @billing.app_service(CommandRunNightly)
    class RunNightly(Pipeline, ApplicationService):
        steps = [CommandCollect, CommandBill, CommandNotify]
        always = CommandClose

A `Pipeline` is declared, not programmed: its `execute` runs the Commands in order, each filled from
the input by the names of its fields. The bus attaches an interceptor that saves the run in the
context (`PipelineRuns.of()`), beside the drafts, wherever the context is stored. Every
default is a default: `records = False` leaves the recorder out, a recipe may be picked by a field
of the input (`by`), the same `run_id` may continue (`resume`), and what a run belongs to — no
tenant and no user, a tenant, a user, a user across tenants — is read from the context and refuses
none.

    domain/        PipelineDeclaration, Resume, Retry, Await — the recipe as data; PipelineRun,
                   StepState and their states; GetRun, FindRuns, ResponseRuns; RunNotFound
    services/      PipelineRuns — the runs of whoever the context says, in its store;
                   PipelineRecorder — the interceptor that writes the run — and `recorders`
    entrypoint/    Pipeline — the ApplicationService base; PipelineReads — the runs read through
                   the bus; BusPipelinesMixin — what a UseFramework adds to attach the recorders

What a project writes grows with what it needs, never before:

    steps = [...]                         the order — the rest is a default
    retry = Retry()                       a step that failed in a way worth it runs again
    Await("approval")                     the run waits for a decision, continued by its input
    PipelineReads                         the runs read on any wire

The run is stored in the context's store, beside the drafts: there is no store of pipelines.
"""

from sincpro_framework.pipelines.domain.reads import FindRuns, GetRun, ResponseRuns
from sincpro_framework.pipelines.domain.recipe import Await, Resume, Retry
from sincpro_framework.pipelines.domain.run import PipelineRun, PipelineState, StepStatus
from sincpro_framework.pipelines.entrypoint.pipeline import Pipeline
from sincpro_framework.pipelines.entrypoint.reads import PipelineReads
from sincpro_framework.pipelines.services.runs import PipelineRuns

__all__ = [
    "Await",
    "FindRuns",
    "GetRun",
    "Pipeline",
    "PipelineReads",
    "PipelineRun",
    "PipelineRuns",
    "PipelineState",
    "ResponseRuns",
    "Resume",
    "Retry",
    "StepStatus",
]
