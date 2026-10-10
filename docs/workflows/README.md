# Workflows — experimental: Commands composed as data

A workflow composes the Commands a bus already answers, as JSON: which runs, with what, in which
order, under which condition. It is validated against the live bus, runs with a trace of every
step, and draws itself as a graph of nodes — what an editor needs to preview a composition, and
what an agent needs to try one. Use cases themselves stay code, or are loaded at runtime with
`sincpro_framework.runtime.runtime_use_cases`; a workflow orchestrates either.

**Experimental:** the vocabulary may change. `sincpro_framework.runtime.workflows` is opt-in and needs
nothing beyond the bus. Every block on this page runs, in order, in
`tests/docs/test_persistence_guide.py`.

## The bus it runs on

```python
import json
from pathlib import Path

from sincpro_framework import DataTransferObject, Feature, UseFramework


class CommandGetOrder(DataTransferObject):
    order_id: int


class ResponseGetOrder(DataTransferObject):
    order_id: int
    total: int
    lines: list[dict]


class CommandCreateInvoice(DataTransferObject):
    order_id: int
    total: int


class ResponseCreateInvoice(DataTransferObject):
    invoice_id: str


class CommandRequestApproval(DataTransferObject):
    invoice_id: str


billing = UseFramework("billing-workflows", log_after_execution=False)
done: list[str] = []


@billing.feature(CommandGetOrder)
class GetOrder(Feature):
    def execute(self, dto: CommandGetOrder) -> ResponseGetOrder:
        total = 15_000 if dto.order_id == 1 else 300
        return ResponseGetOrder(order_id=dto.order_id, total=total, lines=[{"sku": "chair"}, {"sku": "desk"}])


@billing.feature(CommandCreateInvoice)
class CreateInvoice(Feature):
    def execute(self, dto: CommandCreateInvoice) -> ResponseCreateInvoice:
        done.append(f"invoice {dto.total}")
        return ResponseCreateInvoice(invoice_id=f"F-{dto.order_id}")


@billing.feature(CommandRequestApproval)
class RequestApproval(Feature):
    def execute(self, dto: CommandRequestApproval) -> None:
        done.append(f"approval {dto.invoice_id}")
```

## A workflow is JSON

```python
bill_order = {
    "name": "bill_order",
    "description": "Invoice an order; orders over 10 000 need an approval.",
    "input": {"order_id": "integer"},
    "steps": [
        {"id": "order", "execute": "CommandGetOrder", "input": {"order_id": "$input.order_id"}},
        {
            "id": "discount",
            "code": "total = steps['order']['total']\nreturn {'total': total * 95 // 100 if total > 10000 else total}",
            "returns": ["total"],
        },
        {
            "id": "invoice",
            "execute": "CommandCreateInvoice",
            "input": {"order_id": "$input.order_id", "total": "$steps.discount.total"},
        },
        {
            "id": "approval",
            "execute": "CommandRequestApproval",
            "input": {"invoice_id": "$steps.invoice.invoice_id"},
            "when": {"field": "$steps.order.total", "operator": ">=", "value": 10000},
        },
    ],
    "output": {"invoice_id": "$steps.invoice.invoice_id"},
}

Path("workflows").mkdir()
Path("workflows/bill_order.json").write_text(json.dumps(bill_order, indent=2))
```

| A step | What it does |
|---|---|
| `execute` + `input` | a Command or Query of the bus, by name, with its fields |
| `code` + `returns` | a snippet: reads `input`, `steps` (and `item`), returns a mapping with the fields it declares |
| `for_each` + `steps` | runs its steps once per item of a list; `$item` is the item; answers `{"items": [...]}` |
| `fail` | stops the run with its message — a veto |
| `when` (on any step) | a condition in the grammar of `Criteria`, its fields references; not holding, the step is skipped |

A value is a literal or a **whole** reference — `$input.<field>`, `$steps.<id>.<field>`,
`$item.<field>`. There is no templating and no function inside a value: computing is a
snippet's job, so every reference is checked against the schemas before anything runs.

A `when` is one condition, or several with `all`, `any` and `negate` — the same tree a
`Criteria` filter is:

```python
needs_approval = {
    "all": [
        {"field": "$steps.order.total", "operator": ">", "value": 50000},
        {"negate": {"field": "$input.approved", "operator": "=", "value": True}},
    ]
}
```

A `for_each` answers `{"items": [...]}`, one entry per item, each the answers of its steps by
id — `steps["lines"]["items"][0]["reserve"]["reserved"]` in a later snippet, or
`$steps.lines.items` as a whole.

## Put them in force and run one

```python
from sincpro_framework.runtime.workflows import FileWorkflows, StepStatus, Workflows

workflows = Workflows(billing, FileWorkflows(Path("workflows")))
workflows.expose()                                   # CommandRunWorkflow on the bus, before it is built

run = workflows.run("bill_order", {"order_id": 1})

assert run.output == {"invoice_id": "F-1"}
assert done == ["invoice 14250", "approval F-1"]
assert [step.status for step in run.steps] == [StepStatus.RAN] * 4

small = workflows.run("bill_order", {"order_id": 2})
assert small.steps[-1].status == StepStatus.SKIPPED
assert "$steps.order.total is 300" in small.steps[-1].reason
```

Every run is a trace: each step's status, what it received and answered, how long it took, and
why it was skipped or failed — with the version of the workflows it ran on. A run that stops
raises `WorkflowFailed` with its trace in `error.run`; it never answers something that looks like
success.

`workflows.expose()` makes it one more Command, so every entrypoint that serves the bus — MCP,
JSON-RPC, gRPC — runs workflows too:

```python
from sincpro_framework.runtime.workflows import CommandRunWorkflow, ResponseRunWorkflow

answer = billing(CommandRunWorkflow(workflow="bill_order", input={"order_id": 2}), ResponseRunWorkflow)
assert answer is not None and answer.output == {"invoice_id": "F-2"}
```

## Change them while the process serves

```python
in_force = Path("workflows/bill_order.json").read_text()
edited = dict(bill_order, steps=[{"id": "x", "execute": "CommandThatDoesNotExist"}])
Path("workflows/bill_order.json").write_text(json.dumps(edited))

issues = workflows.reload()                          # checked as a whole, against this bus

assert "CommandThatDoesNotExist" in issues[0].message
assert workflows.run("bill_order", {"order_id": 2}).output == {"invoice_id": "F-2"}   # the valid set serves

Path("workflows/bill_order.json").write_text(in_force)
assert workflows.refresh() is False                  # the source is back to the version in force
```

- A set with any issue never replaces the one in force; `reload()` answers the issues.
- `refresh()` reloads only when the source's version changed — cheap enough for a cron:

```python
from datetime import timedelta

from sincpro_framework.entrypoints.adapters.cron import Cron, Crons, Tick

cron_workflows = Crons("cron-workflows")
cron_workflows.add_dependency("workflows", workflows)


@cron_workflows.cron(every=timedelta(seconds=5))
class RefreshWorkflows(Cron):
    workflows: Workflows

    def run(self, tick: Tick) -> None:
        self.workflows.refresh()
```

- A run takes the set in force when it starts and finishes on it.
- WorkflowSet come from any `WorkflowSource`: `FileWorkflows` (reviewed in a PR),
  `InMemoryWorkflows`, or one the project writes over its own table for tenant-edited ones.

## For an agent writing one

```python
schema = Workflows.schema()                          # the JSON Schema of a workflow
catalog = workflows.catalog()                        # every Command, its input and response schemas
assert "CommandGetOrder" in catalog and "order_id" in catalog["CommandGetOrder"]["input"]["properties"]

draft = {
    "name": "draft",
    "input": {"order_id": "integer"},
    "steps": [
        {"id": "invoice", "execute": "CommandCreateInvoice", "input": {"order_id": "$input.order_id", "total": "$steps.order.total"}},
        {"id": "note", "code": "return {'text': orders}", "returns": ["text"]},
    ],
}
problems = [str(issue) for issue in workflows.validate(draft)]
assert any("$steps.order" in one for one in problems)            # a step that is not before it
assert any("orders" in one for one in problems)                  # a name the snippet does not have

rehearsal = workflows.dry_run(
    "bill_order",
    {"order_id": 9},
    responses={
        "CommandGetOrder": {"order_id": 9, "total": 50, "lines": []},
        "CommandCreateInvoice": {"invoice_id": "DRY-9"},
    },
)
assert rehearsal.output == {"invoice_id": "DRY-9"}               # nothing ran on the bus

draft_rehearsal = workflows.dry_run(                             # a draft, before it is stored
    {"name": "check", "steps": [{"id": "order", "execute": "CommandGetOrder", "input": {"order_id": 3}}]},
    {},
    responses={"CommandGetOrder": [{"order_id": 3, "total": 1, "lines": []}]},   # a list: one per call
)
assert draft_rehearsal.steps[0].output["total"] == 1

print(workflows.draw("bill_order"))                              # a Mermaid flowchart
```

The loop that works: read `schema()` and `catalog()`, write the definition, `validate()` it —
every issue comes back at once, each naming the step, the path and what is accepted — fix,
validate again, `dry_run()` the draft with the responses it should get (a list when a Command is
called once per item), then store it. A `fail` step's message is its `reason` in the trace.
`SINCPRO_FRAMEWORK_LOG_LEVEL=INFO` keeps the framework's debug lines out of what you read.

## Snippets are open

A snippet is Python, run in this process by `PythonSnippets`: checked with `ast` when it is
loaded (syntax, a `return`, every name it reads), compiled under `snippet://<workflow>/<step>`
so a traceback shows its own lines. What it may do is the project's decision — the framework
gives the mechanism, not a cage. It cannot be stopped once it runs: a project that needs
isolation or a hard timeout implements `SnippetEngine` over a subprocess, CEL or WASM, and every
workflow keeps working.

A run is bounded — `Workflows(bus, source, Limits(max_steps=1000, max_items=1000, max_depth=5))`,
those being the defaults: steps per run, items in a `for_each`, and workflows running workflows.
A definition that loops stops with a message. A string value that starts with `$` is always a
reference; a literal that must start with one comes from a snippet.
