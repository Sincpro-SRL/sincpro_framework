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
memory sources, `services/` a run and its validation, and `entrypoint/` `Workflows`, the
component a project uses. What a bus can execute is `sincpro_framework.introspection.operations_of`.
"""

from sincpro_framework.runtime.workflows.adapters.python_snippets import PythonSnippets
from sincpro_framework.runtime.workflows.adapters.sources import (
    FileWorkflows,
    InMemoryWorkflows,
)
from sincpro_framework.runtime.workflows.domain.issue import Issue, WorkflowsInvalid
from sincpro_framework.runtime.workflows.domain.run import (
    CommandRunWorkflow,
    Limits,
    ResponseRunWorkflow,
    RunStatus,
    StepRun,
    StepStatus,
    WorkflowFailed,
    WorkflowRun,
)
from sincpro_framework.runtime.workflows.domain.snippets import SnippetEngine
from sincpro_framework.runtime.workflows.domain.source import (
    RawWorkflow,
    WorkflowSet,
    WorkflowSource,
)
from sincpro_framework.runtime.workflows.domain.workflow import Step, Workflow
from sincpro_framework.runtime.workflows.entrypoint.workflows import Workflows

__all__ = [
    "CommandRunWorkflow",
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
    "WorkflowSet",
    "WorkflowSource",
    "Workflows",
    "WorkflowsInvalid",
]
