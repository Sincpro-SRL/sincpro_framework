"""`BusPipelinesMixin`: what a `UseFramework` mixes in so each `Pipeline` it answers keeps its runs.

When the bus is built, an interceptor — `PipelineRecorder` — is attached to the Command of every
pipeline and to the steps of its recipes that this bus answers. Nothing is declared: a class that
is a `Pipeline` brings it. What a project may leave out is its `records = False`.
"""

from typing import TYPE_CHECKING

from sincpro_framework.bus_pipeline.interceptors.interceptor import InterceptorRegistration
from sincpro_framework.pipelines.pipeline import Await, Pipeline
from sincpro_framework.pipelines.recorder import PipelineRecorder
from sincpro_framework.sincpro_logger import logger

ENDING = ("ok", "failure")
"""What the `always` Command may take beyond the input."""


class BusPipelinesMixin:
    """What a `UseFramework` adds to attach the recorder of its pipelines."""

    if TYPE_CHECKING:

        @property
        def name(self) -> str: ...

        def feature_handlers(self) -> dict[type, type]: ...

        def app_service_handlers(self) -> dict[type, type]: ...

    def _pipeline_recorders(self) -> list[InterceptorRegistration]:
        """1. Each ApplicationService that is a `Pipeline` and records.
        2. What its recipes ask is said in the logs, never refused: a step this bus does not
           answer, a field the input lacks.
        Final: one recorder per pipeline, wrapping its Command and the steps this bus answers — an
        interceptor cannot wrap what the bus does not answer.

        Context: read at every build and never registered, so each generation of the bus wraps
        its handlers with recorders of its own."""
        recorders: list[InterceptorRegistration] = []
        features = self.feature_handlers()
        pipelines = self.app_service_handlers()
        answered = {*features, *pipelines}
        for command, handler in pipelines.items():
            if not (
                isinstance(handler, type)
                and issubclass(handler, Pipeline)
                and handler.records
            ):
                continue
            declared = [step for recipe in handler.recipes() for step in recipe]
            for waits in declared:
                if isinstance(waits, Await) and waits.name not in getattr(
                    command, "model_fields", {}
                ):
                    logger.warning(
                        f"pipeline {handler.__name__}: it waits for '{waits.name}', which "
                        f"{command.__name__} has no field to be run again with"
                    )
            steps = [step for step in declared if isinstance(step, type)]
            if handler.always is not None:
                steps.append(handler.always)
            wrapped: list[type] = [command]
            for step in steps:
                if step not in answered:
                    logger.warning(
                        f"pipeline {handler.__name__}: step {step.__name__} is not answered by "
                        f"'{self.name}': it fails when the run reaches it"
                    )
                elif step not in wrapped:
                    wrapped.append(step)
                for field, info in getattr(step, "model_fields", {}).items():
                    taken = {
                        *getattr(command, "model_fields", {}),
                        *(ENDING if step is handler.always else ()),
                    }
                    if info.is_required() and field not in taken:
                        logger.warning(
                            f"pipeline {handler.__name__}: step {step.__name__} needs '{field}', "
                            f"which {command.__name__} does not have"
                        )
            recorders.append(
                InterceptorRegistration(PipelineRecorder(handler, command), tuple(wrapped))
            )
        return recorders
