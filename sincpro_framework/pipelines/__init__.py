"""Pipelines: an ApplicationService that is the order of its steps, and keeps how far each run got.

    from sincpro_framework.pipelines import Pipeline, Resume

    @billing.app_service(CommandRunNightly)
    class RunNightly(Pipeline, ApplicationService):
        steps = [CommandCollect, CommandBill, CommandNotify]
        always = CommandClose

A `Pipeline` is declared, not programmed: its `execute` runs the Commands in order, each filled from
the input by the names of its fields. The bus attaches an interceptor that keeps the run in the
context (`use_context().pipelines()`), beside the drafts, wherever the context is kept. Every
default is a default: `records = False` leaves the recorder out, a recipe may be picked by a field
of the input (`by`), the same `run_id` may continue (`resume`), and what a run belongs to — no
tenant and no user, a tenant, a user, a user across tenants — is read from the context and refuses
none.

    pipeline.py      Pipeline, Resume — the declaration and the order of the steps
    recorder.py      PipelineRecorder — the interceptor that writes the run
    run.py           PipelineRun, StepState — the run as the context keeps it
    runs.py          PipelineRuns — the runs of whoever the context says, in its store
    mixins/          what a UseFramework adds to attach the recorder

The run is kept in the context's store, beside the drafts: there is no store of pipelines.
"""

from sincpro_framework.pipelines.pipeline import Pipeline, Resume
from sincpro_framework.pipelines.run import PipelineRun, PipelineState, StepState, StepStatus

__all__ = [
    "Pipeline",
    "PipelineRun",
    "PipelineState",
    "Resume",
    "StepState",
    "StepStatus",
]
