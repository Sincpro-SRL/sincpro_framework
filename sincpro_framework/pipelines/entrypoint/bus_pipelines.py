"""`BusPipelinesMixin`: what a `UseFramework` mixes in so each `Pipeline` it answers saves its runs.

When the bus is built, an interceptor — `PipelineRecorder` — is attached to the Command of every
pipeline and to the steps of its recipes that this bus answers. Nothing is declared: a class that
is a `Pipeline` brings it. What a project may leave out is its `records = False`.
"""

from typing import TYPE_CHECKING

from sincpro_framework.bus_pipeline.interceptors.interceptor import InterceptorRegistration
from sincpro_framework.pipelines.services.recorder import recorders


class BusPipelinesMixin:
    """What a `UseFramework` adds to attach the recorder of its pipelines."""

    if TYPE_CHECKING:

        @property
        def name(self) -> str:
            """The bus's name — declared for the type checker; `UseFramework` defines it."""
            ...

        def feature_handlers(self) -> dict[type, type]:
            """Each DTO and the Feature that answers it — `UseFramework` defines it."""
            ...

        def app_service_handlers(self) -> dict[type, type]:
            """Each DTO and the ApplicationService that answers it — `UseFramework` defines
            it."""
            ...

    def _pipeline_recorders(self) -> list[InterceptorRegistration]:
        """The recorders the pipelines of this bus bring — one interceptor per pipeline, over
        what this bus answers (`recorders`).

        Context: read at every build and never registered, so each generation of the bus wraps
        its handlers with recorders of its own."""
        pipelines = self.app_service_handlers()
        return recorders(pipelines, {**self.feature_handlers(), **pipelines}, self.name)
