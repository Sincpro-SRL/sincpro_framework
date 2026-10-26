"""`PipelineRuns`: the runs of the pipelines of whoever the context says, stored in the context's store.

    runs = PipelineRuns.of()                               this tenant's — or nobody's, without one
    runs.read("billing.CommandRunNightly", "2026-10-23")
    runs.find("billing.CommandRunNightly", state=PipelineState.FAILED)

A run is an entry of the context's store, under one namespace, one owner and one pipeline:

    SINCPRO_PIPELINE_CONTEXT : tenant_id=acme,tenant_ids= : billing.CommandRunNightly : 2026-10-23
    └─ namespace               └─ owner (`Owner.of`)        └─ the pipeline            └─ the run

N runs live side by side, each its own entry with its own version and TTL: two runs — on two
replicas, at the same instant — never write the same one. The owner is the tenant keys, and the
keys of `by` a pipeline says its runs belong to (`owned_by`).
"""

from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote

from sincpro_framework.context import (
    Context,
    ContextKey,
    ContextStore,
    Entry,
    Owner,
    use_context,
)
from sincpro_framework.data_layer.store import LeaseState
from sincpro_framework.pipelines.domain.run import PipelineRun, PipelineState


class PipelineRuns:
    """The runs of one owner — the values of `by`, read from a context: the tenant's by
    default."""

    def __init__(
        self,
        store: ContextStore,
        context: Mapping[Any, Any],
        by: Sequence[str] = Owner.BY_TENANT,
    ) -> None:
        """The runs stored in `store` for the owner `context` names by the keys of `by` — read
        once, here."""
        self.store = store
        self.owner = Owner.of(context, by)
        """Whose these runs are: `tenant_id=acme,tenant_ids=`."""

    @classmethod
    def of(
        cls,
        context: Context | None = None,
        by: Sequence[str] = Owner.BY_TENANT,
        store: ContextStore | None = None,
    ) -> "PipelineRuns":
        """The runs of whoever `context` says — the context in play when none is given — in
        `store`, else where that context stores what it owns.

            PipelineRuns.of()                                    ->  this tenant's runs
            PipelineRuns.of(by=Owner.BY_TENANT_AND_USER)         ->  this tenant's and user's
            PipelineRuns.of(context, store=InMemoryContexts())   ->  that context's, in that store
        """
        read = context if context is not None else use_context()
        return cls(read.store_in_use(store), read, by)

    def _folder(self, pipeline: str) -> str:
        """The folder of `pipeline`'s runs for this owner — the pipeline encoded, so a `:` in
        it never splits the key.

            _folder("billing.CommandRunNightly")
            ->  "SINCPRO_PIPELINE_CONTEXT:<owner>:billing.CommandRunNightly"
        """
        return f"{ContextKey.PIPELINE_CONTEXT}:{self.owner}:{quote(pipeline, safe='')}"

    def _run(self, entry: Entry) -> PipelineRun:
        """The `PipelineRun` an entry holds, its `store_version` the entry's version — what
        the next `save` names as `expected`."""
        return PipelineRun.model_validate({**entry.values, "store_version": entry.version})

    def save(self, run: PipelineRun, expected: int = 0) -> PipelineRun:
        """`run` as the state of its pipeline and `run_id`, under a new version, when `expected`
        is the version there now — `0` for none; anything else raises `EntryConflict`."""
        owned = run.model_copy(update={"owner": self.owner})
        saved = self.store.save_entry(
            f"{self._folder(run.pipeline)}:{quote(run.run_id, safe='')}",
            owned.model_dump(mode="json", exclude={"store_version"}),
            expected,
        )
        return self._run(saved)

    def read(self, pipeline: str, run_id: str) -> PipelineRun | None:
        """The live run, or `None` when there is none, it was forgotten or it expired."""
        found = self.store.get_entry(f"{self._folder(pipeline)}:{quote(run_id, safe='')}")
        return None if found is None else self._run(found)

    def forget(self, pipeline: str, run_id: str) -> None:
        """Drop the run; one with none is not an error."""
        self.store.delete_entry(f"{self._folder(pipeline)}:{quote(run_id, safe='')}")

    def find(
        self, pipeline: str, state: PipelineState | None = None, limit: int = 100
    ) -> list[PipelineRun]:
        """The runs of `pipeline`, the most recently started first — those in `state` when named."""
        found = [
            self._run(entry)
            for key in self.store.list_entries(self._folder(pipeline))
            if (entry := self.store.get_entry(key)) is not None
        ]
        chosen = [run for run in found if state is None or run.state == state]
        chosen.sort(key=lambda run: run.started_at, reverse=True)
        return chosen[:limit]

    def lease_name(self, pipeline: str, run_id: str) -> str:
        """The lease a run of a pipeline that holds one is driven under — named as its entry is."""
        return f"{self._folder(pipeline)}:{quote(run_id, safe='')}"

    def abandoned(self, pipeline: str, limit: int = 100) -> list[PipelineRun]:
        """The runs still `RUNNING` whose worker stopped renewing their lease — died, or paused
        past it. A worker may still be at work on one: what a reclaimer marks failed and leaves
        what it held for a person, rather than handing it to another worker at once."""
        leases = self.store.leases()
        return [
            run
            for run in self.find(pipeline, state=PipelineState.RUNNING, limit=limit)
            if leases.state(self.lease_name(pipeline, run.run_id)) is LeaseState.EXPIRED
        ]
