"""`BusPipelinesMixin`: what a `UseFramework` mixes in so each `Pipeline` it answers keeps its runs.

When the bus is built, an interceptor — `PipelineRecorder` — is attached to the Command of every
pipeline and to the steps of its recipes that this bus answers. Nothing is declared: a class that
is a `Pipeline` brings it. What a project may leave out is its `records = False`.
"""

from typing import TYPE_CHECKING

from sincpro_framework.bus_pipeline.interceptors.interceptor import InterceptorRegistration
from sincpro_framework.pipelines.pipeline import Pipeline
from sincpro_framework.pipelines.recorder import PipelineRecorder
from sincpro_framework.sincpro_logger import logger

ENDING = ("ok", "failure")
"""What the `always` Command may take beyond the input."""


class BusPipelinesMixin:
    """What a `UseFramework` adds to attach the recorder of its pipelines."""

    _interceptors: list[InterceptorRegistration]

    if TYPE_CHECKING:

        @property
        def name(self) -> str: ...

        def feature_handlers(self) -> dict[type, type]: ...

        def app_service_handlers(self) -> dict[type, type]: ...

    def _attach_pipelines(self) -> None:
        """1. Each ApplicationService that is a `Pipeline` and records.
        2. What its recipes ask is said in the logs, never refused: a step this bus does not
           answer, a field the input lacks.
        Final: the recorder wraps its Command and the steps this bus answers — an interceptor
        cannot wrap what the bus does not answer.

        Context: attached at build and not registered, so a `fresh()` generation attaches its own
        when it is built."""
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
            steps = [step for recipe in handler.recipes() for step in recipe]
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
            self._interceptors.append(
                InterceptorRegistration(PipelineRecorder(handler, command), tuple(wrapped))
            )
