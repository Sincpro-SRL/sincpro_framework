# Pipelines: an ApplicationService that is the order of its steps

A job that takes several steps — collect, bill, notify; prepare a workspace, run an agent, verify,
deliver — is an ApplicationService whose `execute` is always the same: run these Commands in this
order, stop when one fails, close either way, and leave a record of how far it got. A `Pipeline` is
that, declared as data, with the record kept in the context. See also
[the execution path](execution-path.md).

Everything on this page is a default. A project may leave any of it: the framework asks for no tenant,
no user, no store and no recipe it can refuse.

Every block on this page runs, in order, in `tests/docs/test_persistence_guide.py`.

## A recipe

```python
from sincpro_framework import ApplicationService, DataTransferObject, Feature, UseFramework
from sincpro_framework.context import InMemoryContexts, use_context
from sincpro_framework.pipelines import Pipeline, PipelineState, Resume, StepStatus


class CommandRunNightly(DataTransferObject):
    run_id: str
    day: str = "today"


class CommandCollect(DataTransferObject):
    run_id: str


class CommandBill(DataTransferObject):
    run_id: str
    day: str


class CommandClose(DataTransferObject):
    run_id: str
    ok: bool
    failure: str


class StepAnswer(DataTransferObject):
    ok: bool = True
    summary: str = ""


billing = UseFramework("billing-pipelines", log_after_execution=False)
billing.context_store(InMemoryContexts())                  # where the runs are kept
credit = {"enough": False}
did: list[str] = []


@billing.feature(CommandCollect)
class Collect(Feature):
    def execute(self, dto: CommandCollect) -> StepAnswer:
        did.append("collect")
        return StepAnswer()


@billing.feature(CommandBill)
class Bill(Feature):
    def execute(self, dto: CommandBill) -> StepAnswer:
        did.append("bill")
        return StepAnswer() if credit["enough"] else StepAnswer(ok=False, summary="no credit")


@billing.feature(CommandClose)
class Close(Feature):
    def execute(self, dto: CommandClose) -> None:
        did.append(f"close ok={dto.ok}")


@billing.app_service(CommandRunNightly)
class RunNightly(Pipeline, ApplicationService):          # the pipeline first: its `execute` runs
    steps = [CommandCollect, CommandBill]
    always = CommandClose                                  # last, whatever happened
    resume = Resume.CONTINUE                               # the same run_id goes on where it stopped


run = billing(CommandRunNightly(run_id="2026-10-23"))

assert did == ["collect", "bill", "close ok=False"]
assert run.state is PipelineState.FAILED and run.error == "no credit"
assert [(one.command.split(".")[-1], one.status) for one in run.steps] == [
    ("CommandCollect", StepStatus.SUCCEEDED),
    ("CommandBill", StepStatus.FAILED),
    ("CommandClose", StepStatus.SUCCEEDED),
]
```

- **The order is data.** `steps` is a list of Commands, each a Feature of the same bus.
- **A step is filled by the names of its fields**, from the input — `run_id`, `day` — so it needs
  nothing from the step before. What a step hands on is kept where the project keeps its data.
- **A step that answers `ok=False` ends the run as failed**; one that raises ends it with the
  exception. Either way `always` runs, and may take `ok` and `failure`.
- **No `ok` is an `ok`**: any Feature is a step, answering anything or nothing.

## The same run, again

```python
credit["enough"] = True
did.clear()

again = billing(CommandRunNightly(run_id="2026-10-23"))

assert did == ["bill", "close ok=True"]              # collect was done: the run goes on after it
assert again.state is PipelineState.SUCCEEDED
```

`resume` says what the same `run_id` does: `Resume.RESTART` (the default) runs every step again;
`Resume.CONTINUE` goes on from the first step that did not succeed, when the recipe is the one it
ran — a run kept under another recipe starts over, and says so in the logs. A call with no `run_id`
is a run of its own.

## Where the run is kept

A run is a `PipelineRun` — the pipeline, the `run_id`, the input, how each step ended with its error
and its kind of failure, and when — kept in the context's store, beside the [drafts](drafts.md): the
one store the context has. It holds the progress of a run and nothing of what the steps did. Each run
is an entry of its own:

```text
SINCPRO_PIPELINE_CONTEXT : tenant_id=acme,tenant_ids= : billing.CommandRunNightly : 2026-10-23
└─ namespace               └─ owner                     └─ the pipeline            └─ the run
```

so N runs — on N replicas, at the same instant — never write the same one.

```python
with billing.context({}):
    kept = use_context().pipelines().find("billing-pipelines.CommandRunNightly")

assert [(one.run_id, one.state) for one in kept] == [("2026-10-23", PipelineState.SUCCEEDED)]
```

| Where | How |
|---|---|
| The memory of the process | `bus.context_store(InMemoryContexts())` — and with no store at all, the same, said once in the logs |
| Redis, Valkey, Memcached | `bus.context_store(KeyValueContexts(RedisKeyValue(redis)))` |
| A project's own storage | its own `ContextStore`, with its entries — `keep_entry`, `entry`, `discard_entry`, `entries` |

There is no store of pipelines: whatever keeps the context keeps the runs. A run lasts the store's
`kept_ttl` from its last write. Every write names the version it read, and a second writer of the
same run — another replica — loses with `EntryConflict` instead of overwriting it in silence; the
run that lost fails, out loud. The run being driven is kept on its own execution, so each change of
a step is one write to the store and no read.

## Whose a run is

The context says, by the rule a draft uses — and it serves every shape a system has:

| The context says | The owner of a run |
|---|---|
| nothing: no tenant, no user | `tenant_id=,tenant_ids=` |
| a tenant | `tenant_id=acme,tenant_ids=` |
| a user — with `owned_by = ("user_id",)` | `tenant_id=acme,tenant_ids=,user_id=ana` |
| a user across several tenants | `tenant_id=,tenant_ids=acme\|globex,user_id=ana` |

A value that is `None`, empty or missing says nothing. The tenant keys always take part, so a run
never crosses tenants; the keys of `owned_by` are whatever else a project says a run belongs to.
Values are encoded: no value is ever read as a separator.

## What a run says about everything it executes

While a run executes, its context carries one key of the framework's, apart from every key a project
uses:

```text
SINCPRO_PIPELINE_CONTEXT: {"pipeline": "billing.CommandRunNightly", "run_id": "2026-10-23"}
```

It lives on the node of the pipeline's execution, so its steps read it and nothing else does: two
runs at the same time are two branches of the context, each with its own. It travels — every step's
logs, spans and [events](outcomes.md) say it, and so does the failure that escapes: its `use_case`
is the step that failed, and its `context` what the run carried there.

## Several recipes

```python
from enum import StrEnum


class Stage(StrEnum):
    REFINE = "refine"
    IMPLEMENT = "implement"


class CommandRunStaged(DataTransferObject):
    run_id: str
    day: str = "today"
    stage: Stage


staged = billing.fresh()                                  # a new generation of the bus, not built yet


@staged.app_service(CommandRunStaged)
class RunStaged(Pipeline, ApplicationService):
    by = "stage"                                          # the field of the input that picks
    steps = {
        Stage.REFINE: [CommandCollect],
        Stage.IMPLEMENT: [CommandCollect, CommandBill],
    }


credit["enough"] = True
did.clear()
staged(CommandRunStaged(run_id="a", stage=Stage.REFINE))

assert did == ["collect"]
```

A recipe the input does not pick is a `ProgrammingError` that says which ones it has: it cannot run.

## What is left to the project

- **`records = False`** leaves the recorder out: the pipeline is then only the order of its steps.
- **`answer(dto, failure)`** may be overridden to answer another response than the `PipelineRun`.
- **A recipe the bus cannot run** — a step it does not answer, a field the input lacks — is said in
  the logs when the bus is built and refused nowhere.
- **A cron** calls a pipeline like any Command, with a `run_id` that comes from its tick: the same
  tick, run again, is the same run.

How it is attached: when the bus is built, an interceptor — `PipelineRecorder` — wraps the
pipeline's Command and the steps this bus answers. It is the only one that writes the run, and it
sees the exception of a step that the pipeline catches. It acts only where the context says a run, so
a step called by hand is recorded nowhere.
