"""Validation: every problem of a workflow at once, each naming the step and the path, in words
that say what to do — checked against the live catalog, so nothing is left to fail at run time.

Context: shape first (pydantic), then meaning — a Command the bus does not answer, an input the
Command does not declare or one it requires and nobody gives, a reference to a step that comes
later or a field its step does not answer, a snippet that would not run. A reference whose
target cannot be known — a response the Feature does not declare, the fields of an `$item` —
is let through rather than guessed at.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, get_args

from pydantic import ValidationError

from sincpro_framework.ddd.criteria import Criteria
from sincpro_framework.ddd.exceptions import InvalidCriteria
from sincpro_framework.introspection.operations import Operation
from sincpro_framework.workflows.domain import (
    InputType,
    Reference,
    SnippetEngine,
    Step,
    StepKind,
    Workflow,
    condition_fields,
    condition_values,
    reference_in,
    references_in,
)


@dataclass(frozen=True)
class Issue:
    workflow: str
    step: str
    """The step id — empty for the workflow itself."""

    path: str
    """Where in the step: `input.total`, `when`, `code`…"""

    message: str

    def __str__(self) -> str:
        where = ".".join(part for part in (self.step, self.path) if part)
        return f"{self.workflow} {where}: {self.message}"


RUN_WORKFLOW = "CommandRunWorkflow"


def _all_steps(steps: Sequence[Step]) -> list[Step]:
    return [one for step in steps for one in [step, *_all_steps(step.steps)]]


@dataclass
class _Scope:
    """What the steps so far answer, for the references of the next one."""

    inputs: set[str]
    outputs: dict[str, set[str] | None] = field(default_factory=dict)
    in_item: bool = False


def _first_problem(error: ValidationError | InvalidCriteria) -> str:
    return error.errors()[0]["msg"] if isinstance(error, ValidationError) else str(error)


def _repaired(raw: Any) -> Any:
    """`raw` with each input type it does not know read as `object` — so the steps of a workflow
    whose inputs are misspelled are still checked, and every issue comes back at once."""
    if not isinstance(raw, Mapping) or not isinstance(raw.get("input"), Mapping):
        return raw
    known = get_args(InputType.__value__)
    inputs = {
        name: kind if kind in known else "object" for name, kind in raw["input"].items()
    }
    return {**raw, "input": inputs}


def _shape_issues(name: str, raw: Any) -> tuple[Workflow | None, list[Issue]]:
    """The workflow as its model — read from `_repaired(raw)` when only its inputs are wrong —
    and the issues of its shape."""
    try:
        return Workflow.model_validate(raw), []
    except ValidationError as error:
        issues = [
            Issue(name, "", ".".join(str(part) for part in problem["loc"]), problem["msg"])
            for problem in error.errors()
        ]
    try:
        return Workflow.model_validate(_repaired(raw)), issues
    except ValidationError:
        return None, issues


class WorkflowValidator:
    def __init__(self, catalog: Mapping[str, Operation], snippets: SnippetEngine) -> None:
        self.catalog = catalog
        self.snippets = snippets

    def _reference_issue(self, reference: Reference, scope: _Scope) -> str | None:
        if reference.root == "input":
            if not reference.fields or reference.fields[0] not in scope.inputs:
                return (
                    f"{reference.text} is not an input of the workflow — its inputs are "
                    f"{', '.join(sorted(scope.inputs)) or 'none'}"
                )
            return None
        if reference.root == "item":
            return (
                None
                if scope.in_item
                else f"{reference.text}: $item exists only in a for_each"
            )
        if reference.root != "steps":
            return f"{reference.text}: a reference starts with $input, $steps or $item"
        if reference.step not in scope.outputs:
            return (
                f"{reference.text}: ${'steps.' + str(reference.step)} is not a step before this one — "
                f"the steps before are {', '.join(scope.outputs) or 'none'}"
            )
        answered = scope.outputs[reference.step]
        if answered is None or not reference.fields:
            return None
        if reference.fields[0] not in answered:
            return (
                f"{reference.text}: step {reference.step} does not answer {reference.fields[0]} — "
                f"it answers {', '.join(sorted(answered)) or 'nothing'}"
            )
        return None

    def _references(
        self, workflow: str, step: str, path: str, value: Any, scope: _Scope
    ) -> list[Issue]:
        issues = []
        for reference in references_in(value):
            problem = self._reference_issue(reference, scope)
            if problem:
                issues.append(Issue(workflow, step, path, problem))
        return issues

    def _condition(self, workflow: str, step: Step, scope: _Scope) -> list[Issue]:
        if step.when is None:
            return []
        try:
            Criteria.model_validate({"where": step.when})
        except (ValidationError, InvalidCriteria) as error:
            return [
                Issue(workflow, step.id, "when", f"not a condition: {_first_problem(error)}")
            ]
        fields = condition_fields(step.when)
        issues = [
            Issue(workflow, step.id, "when", f"{one}: a condition's field is a reference")
            for one in fields
            if not one.startswith("$")
        ]
        issues += [
            Issue(
                workflow,
                step.id,
                "when",
                f"{value}: a condition's value is a literal — compare two references in a "
                "snippet",
            )
            for value in condition_values(step.when)
            if reference_in(value) is not None
        ]
        return issues + self._references(workflow, step.id, "when", fields, scope)

    def _execute(self, workflow: str, step: Step, scope: _Scope) -> list[Issue]:
        operation = self.catalog.get(step.execute or "")
        if operation is None:
            return [
                Issue(
                    workflow,
                    step.id,
                    "execute",
                    f"the bus does not answer {step.execute} — it answers "
                    f"{', '.join(self.catalog)}",
                )
            ]
        declared = set(operation.input.model_fields)
        issues = [
            Issue(
                workflow,
                step.id,
                f"input.{name}",
                f"not a field of {operation.name} — its "
                f"fields are {', '.join(sorted(declared))}",
            )
            for name in step.input
            if name not in declared
        ]
        missing = sorted(operation.required_input - set(step.input))
        if missing:
            issues.append(
                Issue(
                    workflow,
                    step.id,
                    "input",
                    f"{', '.join(missing)} is required by " f"{operation.name} and not given",
                )
            )
        return issues + self._references(workflow, step.id, "input", step.input, scope)

    def _code(self, workflow: str, step: Step, scope: _Scope) -> list[Issue]:
        names = ["input", "steps"] + (["item"] if scope.in_item else [])
        return [
            Issue(workflow, step.id, "code", problem)
            for problem in self.snippets.check(step.code or "", names)
        ]

    def _step(self, workflow: str, step: Step, scope: _Scope, seen: set[str]) -> list[Issue]:
        """1. The step is one kind, with an id that is an identifier and is unique.
        2. What its kind needs — a Command, a snippet, a list, a message.
        3. Final: its condition, and what it answers for the steps after it.
        """
        issues: list[Issue] = []
        if not step.id.isidentifier():
            issues.append(
                Issue(
                    workflow, step.id, "id", "a step id is an identifier: letters, digits, _"
                )
            )
        if step.id in seen:
            issues.append(Issue(workflow, step.id, "id", f"the id {step.id} is used twice"))
        seen.add(step.id)
        kinds = step.kinds
        if len(kinds) != 1:
            issues.append(
                Issue(
                    workflow,
                    step.id,
                    "",
                    f"a step is one of execute, code, for_each or fail — "
                    f"this one is {', '.join(kinds) or 'none of them'}",
                )
            )
            return issues
        issues += self._condition(workflow, step, scope)
        misplaced = {
            "input": (step.input, StepKind.EXECUTE),
            "returns": (step.returns, StepKind.CODE),
            "steps": (step.steps, StepKind.FOR_EACH),
        }
        issues += [
            Issue(
                workflow,
                step.id,
                field,
                f"{field} is for {kind} steps — this one is {kinds[0]}",
            )
            for field, (value, kind) in misplaced.items()
            if value and kinds[0] != kind
        ]
        match kinds[0]:
            case StepKind.EXECUTE:
                issues += self._execute(workflow, step, scope)
                operation = self.catalog.get(step.execute or "")
                scope.outputs[step.id] = operation.response_fields if operation else None
            case StepKind.CODE:
                issues += self._code(workflow, step, scope)
                scope.outputs[step.id] = set(step.returns)
            case StepKind.FOR_EACH:
                issues += self._references(
                    workflow, step.id, "for_each", step.for_each, scope
                )
                inner = _Scope(scope.inputs, dict(scope.outputs), in_item=True)
                for child in step.steps:
                    issues += self._step(workflow, child, inner, seen)
                scope.outputs[step.id] = {"items"}
            case StepKind.FAIL:
                scope.outputs[step.id] = set()
        return issues

    def validate(self, raw: Any, origin: str = "") -> list[Issue]:
        name = raw.get("name", origin) if isinstance(raw, Mapping) else origin
        workflow, issues = _shape_issues(str(name), raw)
        if workflow is None:
            return issues
        scope = _Scope(set(workflow.input))
        seen: set[str] = set()
        for step in workflow.steps:
            issues += self._step(workflow.name, step, scope, seen)
        issues += self._references(workflow.name, "", "output", workflow.output, scope)
        return issues

    def validate_all(self, raws: Sequence[tuple[str, Any]]) -> list[Issue]:
        issues: list[Issue] = []
        names: dict[str, str] = {}
        for origin, raw in raws:
            issues += self.validate(raw, origin)
            name = raw.get("name") if isinstance(raw, Mapping) else None
            if isinstance(name, str) and name in names:
                issues.append(
                    Issue(name, "", "name", f"defined twice: in {names[name]} and {origin}")
                )
            elif isinstance(name, str):
                names[name] = origin
        return issues + self._run_workflow_issues(raws, set(names))

    def _run_workflow_issues(
        self, raws: Sequence[tuple[str, Any]], names: set[str]
    ) -> list[Issue]:
        """A step that executes `CommandRunWorkflow` with a literal name runs one of the set."""
        issues = []
        for _, raw in raws:
            workflow, _ = _shape_issues("", raw)
            for step in _all_steps(workflow.steps if workflow else []):
                target = step.input.get("workflow") if step.execute == RUN_WORKFLOW else None
                if (
                    isinstance(target, str)
                    and reference_in(target) is None
                    and target not in names
                ):
                    issues.append(
                        Issue(
                            workflow.name if workflow else "",
                            step.id,
                            "input.workflow",
                            f"{target} is not a workflow of this set — it has {', '.join(sorted(names))}",
                        )
                    )
        return issues
