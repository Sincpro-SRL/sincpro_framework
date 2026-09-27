"""Running a workflow: each step in order, its references resolved, its condition evaluated with
the filter `Criteria` answers in memory, and a trace of every step.

Context: a run is bounded — at most `max_steps` steps, `max_items` items in a `for_each`, and
`max_depth` workflows running inside one another (a workflow may execute `CommandRunWorkflow`) —
so a definition that loops stops with a message instead of taking the process down.
"""

import copy
import traceback
from collections.abc import Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from functools import partial
from time import perf_counter
from typing import Any, NoReturn

from sincpro_framework.ddd.criteria import Criteria, holds
from sincpro_framework.workflows.domain import (
    RunStatus,
    SnippetEngine,
    Step,
    StepKind,
    StepRun,
    StepStatus,
    UnresolvedReference,
    Workflow,
    WorkflowFailed,
    WorkflowRun,
    condition_fields,
    reference_in,
    resolve,
    resolve_all,
)

type Execute = Callable[[str, dict[str, Any]], dict[str, Any]]
"""Execute a Command by name with its fields; answer its response as a mapping."""

_depth: ContextVar[int] = ContextVar("sincpro_workflow_depth", default=0)

INPUT_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "object": (dict,),
    "array": (list,),
}


@dataclass(frozen=True)
class Limits:
    max_steps: int = 1000
    max_items: int = 1000
    max_depth: int = 5


class _Stop(Exception):
    """A step failed, or a `fail` step or a guard stopped the run."""


class _Veto(_Stop):
    """A `fail` step: a business rule said no — its message is the whole story."""


def _input_problems(workflow: Workflow, given: Mapping[str, Any]) -> list[str]:
    problems = []
    for name, kind in workflow.input.items():
        if name not in given:
            problems.append(f"{name} is required")
            continue
        value = given[name]
        wrong_bool = isinstance(value, bool) and kind != "boolean"
        if wrong_bool or not isinstance(value, INPUT_TYPES[kind]):
            problems.append(f"{name} is {type(value).__name__}, the workflow declares {kind}")
    return problems


def _shown(read: Callable[[str], Any], field: str) -> str:
    """What a condition read, for the reason it did not hold — a field the condition never
    reached, because an earlier part already decided it, is said to be unreached."""
    try:
        return repr(read(field))
    except UnresolvedReference:
        return "not reached"


def _reason(condition: Mapping[str, Any], read: Callable[[str], Any]) -> str:
    values = ", ".join(f"{one} is {_shown(read, one)}" for one in condition_fields(condition))
    return f"its condition did not hold — {values}"


def _read(scope: Mapping[str, Any], field: str) -> Any:
    reference = reference_in(field)
    if reference is None:
        raise UnresolvedReference(f"{field}: a condition's field is a reference")
    return resolve(reference, scope)


def _checked_snippet_answer(step: Step, answer: Any) -> dict[str, Any]:
    answer = {} if answer is None else answer
    if not isinstance(answer, Mapping):
        raise _Stop(
            f"the snippet returned {type(answer).__name__}; a snippet returns a mapping"
        )
    missing = [name for name in step.returns if name not in answer]
    if missing:
        raise _Stop(f"the snippet did not return {', '.join(missing)}, which it declares")
    return dict(answer)


def _described(error: BaseException) -> str:
    return "".join(traceback.format_exception(error)).strip()


def _fail(run: WorkflowRun, message: str) -> NoReturn:
    run.status = RunStatus.FAILED
    run.error = message
    raise WorkflowFailed(message, run)


class WorkflowRunner:
    def __init__(self, execute: Execute, snippets: SnippetEngine, limits: Limits) -> None:
        self.execute = execute
        self.snippets = snippets
        self.limits = limits

    def _holds(self, step: Step, scope: Mapping[str, Any]) -> tuple[bool, str]:
        if step.when is None:
            return True, ""
        expression = Criteria.model_validate({"where": step.when}).expression
        read = partial(_read, scope)
        if holds(expression, read):
            return True, ""
        return False, _reason(step.when, read)

    def _answer(
        self, workflow: Workflow, step: Step, scope: dict[str, Any], run: WorkflowRun
    ) -> Any:
        match step.kinds[0]:
            case StepKind.EXECUTE:
                return self.execute(step.execute or "", resolve_all(step.input, scope))
            case StepKind.CODE:
                values = copy.deepcopy(scope)
                answer = self.snippets.run(
                    step.code or "", f"snippet://{workflow.name}/{step.id}", values
                )
                return _checked_snippet_answer(step, answer)
            case StepKind.FOR_EACH:
                items = resolve_all(step.for_each, scope)
                if not isinstance(items, list):
                    raise _Stop(f"{step.for_each} is {type(items).__name__}, not a list")
                if len(items) > self.limits.max_items:
                    raise _Stop(
                        f"{step.for_each} has {len(items)} items, more than {self.limits.max_items}"
                    )
                answers = []
                for item in items:
                    inner = {**scope, "steps": dict(scope["steps"]), "item": item}
                    self._steps(workflow, step.steps, inner, run)
                    ran = inner["steps"]
                    answers.append(
                        {child.id: ran[child.id] for child in step.steps if child.id in ran}
                    )
                return {"items": answers}
            case StepKind.FAIL:
                raise _Veto(step.fail or "")
        raise _Stop(f"{step.id} is none of execute, code, for_each or fail")

    def _step(
        self, workflow: Workflow, step: Step, scope: dict[str, Any], run: WorkflowRun
    ) -> None:
        """1. Count it against `max_steps`.
        2. Its condition: not holding, it is recorded skipped with the values it read.
        3. Final: its answer, recorded — or the failure, recorded and raised.
        """
        if len(run.steps) >= self.limits.max_steps:
            raise _Stop(f"the run took more than {self.limits.max_steps} steps")
        started = perf_counter()
        try:
            holding, reason = self._holds(step, scope)
            if not holding:
                run.steps.append(
                    StepRun(id=step.id, status=StepStatus.SKIPPED, reason=reason)
                )
                return
            answer = self._answer(workflow, step, scope, run)
        except _Veto as veto:
            run.steps.append(
                StepRun(
                    id=step.id, status=StepStatus.FAILED, reason=str(veto), error=str(veto)
                )
            )
            raise _Stop(f"step {step.id}: {veto}") from veto
        except Exception as error:
            run.steps.append(
                StepRun(
                    id=step.id,
                    status=StepStatus.FAILED,
                    error=_described(error),
                    duration_ms=(perf_counter() - started) * 1000,
                )
            )
            raise _Stop(f"step {step.id} failed: {error}") from error
        scope["steps"][step.id] = answer
        run.steps.append(
            StepRun(
                id=step.id,
                status=StepStatus.RAN,
                input=resolve_all(step.input, scope) if step.execute else None,
                output=answer,
                duration_ms=(perf_counter() - started) * 1000,
            )
        )

    def _steps(
        self, workflow: Workflow, steps: list[Step], scope: dict[str, Any], run: WorkflowRun
    ) -> None:
        for step in steps:
            self._step(workflow, step, scope, run)

    def run(self, workflow: Workflow, version: str, given: Mapping[str, Any]) -> WorkflowRun:
        """Context: a run that stops raises `WorkflowFailed` with its trace — never an answer
        that looks like success.

        1. Refuse input the workflow does not declare the way it declares it.
        2. Refuse a run nested deeper than `max_depth`.
        3. Final: run the steps and map the output.
        """
        run = WorkflowRun(workflow=workflow.name, version=version, input=dict(given))
        problems = _input_problems(workflow, given)
        if problems:
            _fail(run, f"{workflow.name}: its input is wrong — {'; '.join(problems)}")
        depth = _depth.get()
        if depth >= self.limits.max_depth:
            _fail(
                run,
                f"{workflow.name}: workflows nested more than {self.limits.max_depth} deep",
            )
        token = _depth.set(depth + 1)
        try:
            scope: dict[str, Any] = {"input": dict(given), "steps": {}}
            self._steps(workflow, workflow.steps, scope, run)
            run.output = resolve_all(workflow.output, scope)
        except (_Stop, UnresolvedReference) as error:
            _fail(run, f"{workflow.name}: {error}")
        finally:
            _depth.reset(token)
        return run
