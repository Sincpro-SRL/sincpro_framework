"""`Workflows`: the workflows of a bounded context, in force on its bus — loaded, validated as a
whole, run, and replaced while the process serves.

    workflows = Workflows(billing, FileWorkflows(Path(__file__).parent / "workflows"))
    workflows.expose()                           # CommandRunWorkflow on the bus, before it is built

    run = workflows.run("bill_order", {"order_id": 1})
    workflows.refresh()                          # reload when the source changed — from a cron

Context: a set of workflows is checked against the live bus before it is put in force, and a
set with any issue never replaces the one serving; a run takes the set in force when it starts
and finishes on it. Everything an agent needs to write one is here too: `schema()`, `catalog()`,
`validate()`, `dry_run()` and `draw()`.
"""

from collections.abc import Mapping
from threading import Lock
from types import MappingProxyType
from typing import Any

from pydantic import Field

from sincpro_framework.introspection.operations import Operation, operations_of
from sincpro_framework.sincpro_abstractions import DataTransferObject, Feature
from sincpro_framework.use_bus import UseFramework
from sincpro_framework.workflows.adapters import PythonSnippets
from sincpro_framework.workflows.domain import (
    SnippetEngine,
    Step,
    StepKind,
    Workflow,
    WorkflowFailed,
    WorkflowRun,
    WorkflowSet,
    WorkflowSource,
)
from sincpro_framework.workflows.runner import Limits, WorkflowRunner
from sincpro_framework.workflows.validation import Issue, WorkflowValidator


class CommandRunWorkflow(DataTransferObject):
    workflow: str
    input: dict[str, Any] = Field(default_factory=dict)


class ResponseRunWorkflow(DataTransferObject):
    output: dict[str, Any]
    run: WorkflowRun


class WorkflowsInvalid(Exception):
    """The first load found issues: there is no valid set to serve."""

    def __init__(self, issues: list[Issue]) -> None:
        super().__init__("; ".join(str(issue) for issue in issues))
        self.issues = issues


def _mermaid_label(text: str) -> str:
    return text.replace('"', "'")


def _drawn_steps(steps: list[Step], lines: list[str], previous: str | None) -> str | None:
    for step in steps:
        kind = step.kinds[0]
        detail = {
            StepKind.EXECUTE: step.execute,
            StepKind.CODE: "code",
            StepKind.FOR_EACH: f"for each {step.for_each}",
            StepKind.FAIL: f"fail: {step.fail}",
        }[kind]
        condition = "<br/>when " + _mermaid_label(str(step.when)) if step.when else ""
        lines.append(f'  {step.id}["{step.id} · {_mermaid_label(str(detail))}{condition}"]')
        if previous:
            lines.append(f"  {previous} --> {step.id}")
        if kind == StepKind.FOR_EACH and step.steps:
            first = step.steps[0].id
            _drawn_steps(step.steps, lines, None)
            lines.append(f"  {step.id} -. each item .-> {first}")
        previous = step.id
    return previous


class Workflows:
    def __init__(
        self,
        bus: UseFramework,
        source: WorkflowSource,
        limits: Limits | None = None,
        snippets: SnippetEngine | None = None,
    ) -> None:
        self.bus = bus
        self.source = source
        self.limits = limits or Limits()
        self.snippets: SnippetEngine = snippets or PythonSnippets()
        self._current: WorkflowSet | None = None
        self._reloading = Lock()
        self._exposed = False
        self._operations: dict[str, Operation] | None = None

    def _catalog(self) -> dict[str, Operation]:
        """Read once: a built bus does not change."""
        if self._operations is None:
            self._operations = operations_of(self.bus)
        return self._operations

    def _validator(self) -> WorkflowValidator:
        return WorkflowValidator(self._catalog(), self.snippets)

    def _execute_on_bus(self, name: str, fields: dict[str, Any]) -> dict[str, Any]:
        response = self.bus(self._catalog()[name].input.model_validate(fields))
        return response.model_dump() if response is not None else {}

    def _workflow(self, name: str) -> tuple[Workflow, str]:
        current = self.current
        if name not in current.workflows:
            known = ", ".join(current.workflows) or "none"
            raise WorkflowFailed(
                f"no workflow {name} — the ones in force are {known}",
                WorkflowRun(workflow=name, version=current.version),
            )
        return current.workflows[name], current.version

    def _load(self) -> tuple[WorkflowSet | None, list[Issue]]:
        """The source's set, validated as a whole against the bus — or its issues."""
        version = self.source.version()
        raws = self.source.load()
        unreadable = [
            Issue(raw.origin, "", "", f"cannot be read: {raw.error}")
            for raw in raws
            if raw.error
        ]
        readable = [(raw.origin, raw.content) for raw in raws if not raw.error]
        issues = unreadable + self._validator().validate_all(readable)
        if not raws:
            issues.append(
                Issue(
                    type(self.source).__name__,
                    "",
                    "",
                    "the source has no workflow — the set in force keeps serving",
                )
            )
        if issues:
            return None, issues
        workflows = {
            workflow.name: workflow
            for workflow in (Workflow.model_validate(content) for _, content in readable)
        }
        return WorkflowSet(MappingProxyType(workflows), version), []

    @property
    def current(self) -> WorkflowSet:
        """The set in force — loaded the first time it is asked for."""
        if self._current is None:
            loaded, issues = self._load()
            if loaded is None:
                raise WorkflowsInvalid(issues)
            self._current = loaded
        return self._current

    def reload(self) -> list[Issue]:
        """Load the source, validate the whole set against the bus, and put it in force — or
        answer its issues and keep the set that is serving. Empty when it was put in force."""
        with self._reloading:
            loaded, issues = self._load()
            if loaded is not None:
                self._current = loaded
        return issues

    def refresh(self) -> bool:
        """Reload when the source changed since the set in force — cheap enough for a cron every
        few seconds. Answers whether a new set was put in force."""
        if self._current is not None and self.source.version() == self._current.version:
            return False
        return not self.reload()

    def validate(self, raw: Any) -> list[Issue]:
        """Every issue of one definition, against this bus — before storing it."""
        return self._validator().validate(raw)

    def run(self, name: str, input: Mapping[str, Any]) -> WorkflowRun:
        workflow, version = self._workflow(name)
        runner = WorkflowRunner(self._execute_on_bus, self.snippets, self.limits)
        return runner.run(workflow, version, input)

    def dry_run(
        self,
        workflow: str | Mapping[str, Any],
        input: Mapping[str, Any],
        responses: Mapping[str, dict[str, Any] | list[dict[str, Any]]],
    ) -> WorkflowRun:
        """Run a workflow in force — or a draft, validated first — with every Command answered
        from `responses`: one answer for every call, or a list answered in order. Nothing
        executes on the bus."""
        remaining = {
            command: list(answer) if isinstance(answer, list) else answer
            for command, answer in responses.items()
        }

        def answered(command: str, fields: dict[str, Any]) -> dict[str, Any]:
            if command not in remaining:
                raise LookupError(f"{command} has no response in this dry run")
            answer = remaining[command]
            if not isinstance(answer, list):
                return dict(answer)
            if not answer:
                raise LookupError(f"{command} was called more times than it has responses")
            return dict(answer.pop(0))

        if isinstance(workflow, Mapping):
            issues = self.validate(workflow)
            if issues:
                raise WorkflowsInvalid(issues)
            definition, version = Workflow.model_validate(workflow), "draft"
        else:
            definition, version = self._workflow(workflow)
        runner = WorkflowRunner(answered, self.snippets, self.limits)
        return runner.run(definition, version, input)

    def catalog(self) -> dict[str, dict[str, Any]]:
        """Every Command the bus answers, with the JSON Schema of its input and response."""
        return {name: operation.to_json() for name, operation in self._catalog().items()}

    @staticmethod
    def schema() -> dict[str, Any]:
        """The JSON Schema of a workflow definition."""
        return Workflow.model_json_schema()

    def draw(self, name: str) -> str:
        """The workflow as a Mermaid flowchart."""
        workflow, _ = self._workflow(name)
        lines = ["flowchart TD"]
        _drawn_steps(workflow.steps, lines, None)
        return "\n".join(lines)

    def expose(self) -> None:
        """Register `CommandRunWorkflow` on the bus — so every entrypoint that serves the bus
        runs workflows too. Before the bus is built — `catalog()`, `validate()` and `current`
        build it."""
        if self.bus.was_initialized or self._exposed:
            raise RuntimeError(
                "expose() before the bus is built, and once — catalog(), validate(), current and "
                "the first execution build it"
            )
        self._exposed = True
        workflows = self

        @self.bus.feature(CommandRunWorkflow)
        class RunWorkflow(Feature):
            def execute(self, dto: CommandRunWorkflow) -> ResponseRunWorkflow:
                run = workflows.run(dto.workflow, dto.input)
                return ResponseRunWorkflow(output=run.output, run=run)
