# Workflows (experimental)

Deep doc in the framework repo: `docs/workflows/README.md`, PRD_06 (every block there runs as a
test). This page stands alone. **Experimental:** the vocabulary may change.

A workflow composes the Commands a bus already answers, as JSON: which runs, with what, in which
order, under which condition. It is validated against the live bus, runs with a trace of every step,
and draws itself as a graph — what an editor previews or an agent tries. Use cases themselves stay
code, or are loaded with `sincpro_framework.runtime_use_cases`.

## A workflow is JSON

```json
{
  "name": "bill_order",
  "description": "Invoice an order; orders over 10 000 need an approval.",
  "input": {"order_id": "integer"},
  "steps": [
    {"id": "order", "execute": "CommandGetOrder", "input": {"order_id": "$input.order_id"}},
    {"id": "discount", "code": "total = steps['order']['total']\nreturn {'total': total * 95 // 100 if total > 10000 else total}", "returns": ["total"]},
    {"id": "invoice", "execute": "CommandCreateInvoice", "input": {"order_id": "$input.order_id", "total": "$steps.discount.total"}},
    {"id": "approval", "execute": "CommandRequestApproval", "input": {"invoice_id": "$steps.invoice.invoice_id"},
     "when": {"field": "$steps.order.total", "operator": ">=", "value": 10000}}
  ],
  "output": {"invoice_id": "$steps.invoice.invoice_id"}
}
```

| A step | What it does |
|---|---|
| `execute` + `input` | a Command or Query of the bus, by name, with its fields |
| `code` + `returns` | a snippet: reads `input`, `steps` (and `item`), returns a mapping with the fields it declares |
| `for_each` + `steps` | runs its steps once per item of a list; `$item` is the item; answers `{"items": [...]}` |
| `fail` | stops the run with its message — a veto |
| `when` (on any step) | a condition in the grammar of `Criteria`; not holding, the step is skipped |

A value is a literal or a **whole** reference — `$input.<field>`, `$steps.<id>.<field>`,
`$item.<field>`. No templating, no function inside a value: computing is a snippet's job, so every
reference is checked against the schemas before anything runs. A `when` is one condition or `all` /
`any` / `negate`.

## Put them in force and run one

```python
from sincpro_framework.workflows import FileWorkflows, StepStatus, Workflows

workflows = Workflows(billing, FileWorkflows(Path("workflows")))
workflows.expose()                                   # CommandRunWorkflow on the bus, before it is built
run = workflows.run("bill_order", {"order_id": 1})
assert run.output == {"invoice_id": "F-1"}
assert [step.status for step in run.steps] == [StepStatus.RAN] * 4
```

Every run is a trace: each step's status, what it received and answered, how long it took, why it was
skipped or failed, and the version it ran on. A run that stops raises `WorkflowFailed` with the trace
in `error.run` — it never answers something that looks like success.

`workflows.expose()` makes it one more Command, so every entrypoint that serves the bus (MCP, RPC,
gRPC) runs workflows too. Call it once, before the bus is built — `catalog()`, `validate()`,
`current` and the first execution build it; after that it raises `RuntimeError`.

A run is not a transaction: each `execute` is its own call on the bus, and a step that fails
leaves the earlier ones done — nothing retries, resumes or compensates. A step skipped by its
`when` has no output: a later reference to it passes `validate()` and fails the run. The
`execute` name is the DTO class name as the bus registers it (`catalog()` lists them); a
`Workflows` reads that catalog once, from the bus object it was given.

## Change them while the process serves

```python
issues = workflows.reload()     # checked as a whole; a set with any issue never replaces the one in force
workflows.refresh()             # reloads only when the source's version changed — cheap for a cron;
                                # False both when nothing changed and when the new set has issues
```

Sources: `FileWorkflows` (reviewed in a PR), `InMemoryWorkflows`, or one the project writes over its
own table. A run takes the set in force when it starts and finishes on it.

## For an agent writing one

```python
schema = Workflows.schema()                  # the JSON Schema of a workflow
catalog = workflows.catalog()                # every Command, its input and response schemas
problems = [str(i) for i in workflows.validate(draft)]     # every issue at once, each naming the step and path
rehearsal = workflows.dry_run("bill_order", {"order_id": 9}, responses={...})   # nothing runs on the bus
print(workflows.draw("bill_order"))          # a Mermaid flowchart
```

The loop: read `schema()` and `catalog()`, write the definition, `validate()`, fix, validate again,
`dry_run()` the draft with the responses it should get (a list when a Command is called once per
item), then store it.

## Snippets and limits

A snippet is Python, run in this process by `PythonSnippets`, checked with `ast` when loaded and
compiled under `snippet://<workflow>/<step>` so a traceback shows its own lines. What it may do is
the project's decision — the framework gives the mechanism, not a cage. It runs with the service's
permissions and cannot be stopped once running — a snippet that never returns holds its thread. A project that needs isolation
implements `SnippetEngine` over a subprocess/CEL/WASM and every workflow keeps working.

A run is bounded: `Workflows(bus, source, Limits(max_steps=1000, max_items=1000, max_depth=5))`. A
definition that loops stops with a message. A string value starting with `$` is always a reference.
