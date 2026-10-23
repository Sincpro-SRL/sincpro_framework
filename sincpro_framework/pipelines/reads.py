"""The runs of a pipeline, read through the bus — what a screen, an agent or an operator asks: one
run, or the runs in a state. The built-in reads, as `Get` and `Search` are for an entity.

    class QueryNightlyRun(GetRun[CommandRunNightly]): ...
    class QueryNightlyRuns(FindRuns[CommandRunNightly]): ...

    @billing.feature([QueryNightlyRun, QueryNightlyRuns])
    class NightlyRuns(PipelineReads): ...

    billing(QueryNightlyRun(run_id="2026-10-23"))                      →  PipelineRun
    billing(QueryNightlyRuns(state=PipelineState.WAITING))             →  ResponseRuns

Context: each DTO names the pipeline by its Command, so one Feature may answer the reads of several
pipelines. Whose runs are read is the context's, as when they were kept: the tenant, and the keys
of `owned_by` — the same the pipeline declares. Published on a wire like any Query: a REST route,
an MCP tool. A run waiting for a decision is listed by its state, and continued by its own Command.
"""

from collections.abc import Sequence
from typing import Any, ClassVar

from sincpro_framework.common.naming import registered_name
from sincpro_framework.context.entrypoint.facade import use_context
from sincpro_framework.context.infrastructure.tree import current_execution
from sincpro_framework.pipelines import exceptions
from sincpro_framework.pipelines.run import PipelineRun, PipelineState
from sincpro_framework.pipelines.runs import PipelineRuns
from sincpro_framework.sincpro_abstractions import DataTransferObject, Feature


class GetRun[P](DataTransferObject):
    """One run of the pipeline `P` — its Command — by its `run_id`."""

    run_id: str


class FindRuns[P](DataTransferObject):
    """The runs of the pipeline `P`, the most recently started first."""

    state: PipelineState | None = None
    """Only the runs in this state — `WAITING` for the decisions pending."""
    limit: int = 50


class ResponseRuns(DataTransferObject):
    runs: list[PipelineRun]


def pipeline_of(dto: type) -> type:
    """The Command a read names in its base.

    in      QueryNightlyRun(GetRun[CommandRunNightly])     →  out  CommandRunNightly
    """
    for base in dto.__mro__:
        generic = getattr(base, "__pydantic_generic_metadata__", None)
        arguments = generic.get("args", ()) if generic else ()
        if len(arguments) == 1 and isinstance(arguments[0], type):
            return arguments[0]
    raise exceptions.ProgrammingError(
        f"{dto.__name__} does not name its pipeline: "
        f"class {dto.__name__}(GetRun[CommandRunNightly])"
    )


class PipelineReads(Feature):
    """The Feature that answers `GetRun` and `FindRuns` for any pipeline of its bus."""

    owned_by: ClassVar[Sequence[str]] = ()
    """The keys of the context the runs belong to, beside the tenant — the pipeline's own
    `owned_by`."""

    def runs(self) -> PipelineRuns:
        """The runs read: the caller's, in the context's store."""
        return use_context().pipelines(by=self.owned_by)

    def pipeline_name(self, dto: Any) -> str:
        execution = current_execution()
        return registered_name(pipeline_of(type(dto)), execution.bus if execution else "")

    def get(self, dto: Any) -> PipelineRun:
        """The run, or `RunNotFound`."""
        name = self.pipeline_name(dto)
        found = self.runs().read(name, dto.run_id)
        if found is None:
            raise exceptions.RunNotFound(f"run {dto.run_id} of {name} is not kept")
        return found

    def find(self, dto: Any) -> ResponseRuns:
        return ResponseRuns(
            runs=self.runs().find(self.pipeline_name(dto), state=dto.state, limit=dto.limit)
        )

    def execute(self, dto: Any) -> Any:
        match dto:
            case GetRun():
                return self.get(dto)
            case FindRuns():
                return self.find(dto)
        raise exceptions.ProgrammingError(
            f"{type(self).__name__} answers GetRun and FindRuns, not {type(dto).__name__}"
        )
