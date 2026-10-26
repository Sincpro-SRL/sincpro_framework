"""The reads of the runs of a pipeline: one run by its `run_id`, or the runs in a state — each
naming its pipeline by the pipeline's Command in its base.

    class QueryNightlyRun(GetRun[CommandRunNightly]): ...
    class QueryNightlyRuns(FindRuns[CommandRunNightly]): ...

    QueryNightlyRun.pipeline_command()      ->  CommandRunNightly
"""

from typing import Any, ClassVar

from sincpro_framework.pipelines.domain import exceptions
from sincpro_framework.pipelines.domain.run import PipelineRun, PipelineState
from sincpro_framework.sincpro_abstractions import DataTransferObject


class RunsQuery[P](DataTransferObject):
    """What every read of runs is: a DTO that names its pipeline, `P`, by the pipeline's
    Command."""

    _named: ClassVar[type | None] = None
    """The Command written between the brackets — set on the class when it is parametrized,
    inherited by every read declared on it."""

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        """The Command a parametrized base names, read once, when pydantic builds it.

        GetRun[CommandRunNightly]          ->  _named = CommandRunNightly
        class QueryNightlyRun(GetRun[CommandRunNightly])    inherits it
        class QueryWrong(GetRun)           ->  _named stays None
        """
        super().__pydantic_init_subclass__(**kwargs)
        arguments = cls.__pydantic_generic_metadata__["args"]
        if len(arguments) == 1 and isinstance(arguments[0], type):
            cls._named = arguments[0]

    @classmethod
    def pipeline_command(cls) -> type:
        """The Command of the pipeline this read names in its base.

        QueryNightlyRun.pipeline_command()   ->  CommandRunNightly
        GetRun.pipeline_command()            ->  ProgrammingError("GetRun does not name its …")
        """
        if cls._named is None:
            raise exceptions.ProgrammingError(
                f"{cls.__name__} does not name its pipeline: "
                f"class {cls.__name__}(GetRun[CommandRunNightly])"
            )
        return cls._named


class GetRun[P](RunsQuery[P]):
    """One run of the pipeline `P` — its Command — by its `run_id`."""

    run_id: str


class FindRuns[P](RunsQuery[P]):
    """The runs of the pipeline `P`, the most recently started first."""

    state: PipelineState | None = None
    """Only the runs in this state — `WAITING` for the decisions pending."""
    limit: int = 50


class ResponseRuns(DataTransferObject):
    """What `FindRuns` answers: the runs, the most recently started first."""

    runs: list[PipelineRun]
