"""Workflows — experimental: Commands of a bus composed as data, drawn as a graph of nodes.

    workflows = Workflows(billing, FileWorkflows(Path(__file__).parent / "workflows"))
    run = workflows.run("bill_order", {"order_id": 1})
    print(workflows.draw("bill_order"))       # a Mermaid flowchart

A workflow is JSON: steps that `execute` Commands by name, run `code` (snippets), repeat
`for_each` item, or `fail` — each with an optional `when`. It is validated against the live bus
before it is put in force, and a run leaves a trace of every step — what a node editor needs to
preview a composition before anyone relies on it.

Experimental: its vocabulary may change. Open, and opt-in: a snippet is whatever the project's
`SnippetEngine` runs — Python in this process by default — and workflows come from any
`WorkflowSource`. It needs nothing beyond the bus.

`domain/` holds the vocabulary and the contracts, `adapters/` Python snippets and the file and
memory sources, `validation` the issues, `runner` a run, and `registry` the component a project
uses. What a bus can execute is `sincpro_framework.introspection.operations_of`.
"""

from sincpro_framework.workflows.adapters import (
    FileWorkflows,
    InMemoryWorkflows,
    PythonSnippets,
)
from sincpro_framework.workflows.domain import (
    RawWorkflow,
    RunStatus,
    SnippetEngine,
    Step,
    StepRun,
    StepStatus,
    Workflow,
    WorkflowFailed,
    WorkflowRun,
    WorkflowSet,
    WorkflowSource,
)
from sincpro_framework.workflows.registry import (
    CommandRunWorkflow,
    ResponseRunWorkflow,
    Workflows,
    WorkflowsInvalid,
)
from sincpro_framework.workflows.runner import Limits
from sincpro_framework.workflows.validation import Issue

__all__ = [
    "CommandRunWorkflow",
    "WorkflowSource",
    "WorkflowSet",
    "WorkflowsInvalid",
    "FileWorkflows",
    "InMemoryWorkflows",
    "Issue",
    "Limits",
    "PythonSnippets",
    "RawWorkflow",
    "ResponseRunWorkflow",
    "RunStatus",
    "SnippetEngine",
    "Step",
    "StepRun",
    "StepStatus",
    "Workflow",
    "WorkflowFailed",
    "WorkflowRun",
    "Workflows",
]
