"""`PipelineRuns`: the runs of the pipelines of whoever the context says, kept in the context's store.

    runs = use_context().pipelines()                       this tenant's — or nobody's, without one
    runs.read("billing.CommandRunNightly", "2026-10-23")
    runs.find("billing.CommandRunNightly", state=PipelineState.FAILED)

A run is an entry of the context's store, under one namespace, one owner and one pipeline:

    SINCPRO_PIPELINE_CONTEXT : tenant_id=acme,tenant_ids= : billing.CommandRunNightly : 2026-10-23
    └─ namespace               └─ owner (`owner_of`)        └─ the pipeline            └─ the run

N runs live side by side, each its own entry with its own version and TTL: two runs — on two
replicas, at the same instant — never write the same one. The owner is the tenant keys, and the
keys of `by` a pipeline says its runs belong to (`owned_by`).
"""

from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote

from sincpro_framework.context.domain.keys import ContextKey
from sincpro_framework.context.domain.store import ContextStore, Entry
from sincpro_framework.context.entrypoint.owner import owner_of
from sincpro_framework.pipelines.run import PipelineRun, PipelineState


class PipelineRuns:
    """The runs of one owner — the tenant keys, and the values of `by`, read from a context."""

    def __init__(
        self, store: ContextStore, context: Mapping[Any, Any], by: Sequence[str] = ()
    ) -> None:
        self.store = store
        self.owner = owner_of(context, by)
        """Whose these runs are: `tenant_id=acme,tenant_ids=`."""

    def _folder(self, pipeline: str) -> str:
        return f"{ContextKey.PIPELINE_CONTEXT}:{self.owner}:{quote(pipeline, safe='')}"

    def _run(self, entry: Entry) -> PipelineRun:
        return PipelineRun.model_validate({**entry.values, "store_version": entry.version})

    def keep(self, run: PipelineRun, expected: int = 0) -> PipelineRun:
        """`run` as the state of its pipeline and `run_id`, under a new version, when `expected`
        is the version there now — `0` for none; anything else raises `EntryConflict`."""
        owned = run.model_copy(update={"owner": self.owner})
        kept = self.store.keep_entry(
            f"{self._folder(run.pipeline)}:{quote(run.run_id, safe='')}",
            owned.model_dump(mode="json", exclude={"store_version"}),
            expected,
        )
        return self._run(kept)

    def read(self, pipeline: str, run_id: str) -> PipelineRun | None:
        """The live run, or `None` when there is none, it was forgotten or it expired."""
        found = self.store.entry(f"{self._folder(pipeline)}:{quote(run_id, safe='')}")
        return None if found is None else self._run(found)

    def forget(self, pipeline: str, run_id: str) -> None:
        """Drop the run; one with none is not an error."""
        self.store.discard_entry(f"{self._folder(pipeline)}:{quote(run_id, safe='')}")

    def find(
        self, pipeline: str, state: PipelineState | None = None, limit: int = 100
    ) -> list[PipelineRun]:
        """The runs of `pipeline`, the most recently started first — those in `state` when named."""
        found = [
            self._run(entry)
            for key in self.store.entries(self._folder(pipeline))
            if (entry := self.store.entry(key)) is not None
        ]
        chosen = [run for run in found if state is None or run.state == state]
        chosen.sort(key=lambda run: run.started_at, reverse=True)
        return chosen[:limit]
