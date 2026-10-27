"""A recipe, declared as data: the steps in order, what a run does when run again, how a failed
step is retried, the decisions it waits for — and what the declaration asks of the bus.

    steps = [CommandCollect, Await("approval"), CommandBill]
    resume = Resume.CONTINUE
    retry = Retry(attempts=5)

`PipelineDeclaration` holds what a `Pipeline` declares and reads it — which recipe an input runs,
which Commands the recorder wraps, what a bus that runs it would miss — and runs nothing: running
the steps is the `Pipeline`'s.
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Any, ClassVar

from sincpro_framework.common.failures import RETRYABLE
from sincpro_framework.context import Owner
from sincpro_framework.pipelines.domain import exceptions

ENDING = ("ok", "failure")
"""What the `always` Command may take beyond the input."""


class Resume(StrEnum):
    """What a run does when the same `run_id` is run again."""

    RESTART = "restart"
    """Every step again, from the first."""
    CONTINUE = "continue"
    """From the first step that did not succeed — when the recipe is the one it ran."""


@dataclass(frozen=True)
class Await:
    """A step that waits: the run stops here until it is run again with the input's field of
    this name filled — what was decided, by a person or another system."""

    name: str

    @property
    def step(self) -> str:
        """How the run names it among its steps."""
        return f"await:{self.name}"


@dataclass(frozen=True)
class Retry:
    """How a pipeline runs a step again after it failed in a way worth running again as it is.

    Retry()                                           3 attempts, as the error says to wait
    Retry(attempts=5, max_wait=timedelta(minutes=1))  more, and longer
    """

    attempts: int = 3
    """How many times a step is run at most, the first included."""
    backoff: float = 2.0
    """Each wait is this many times the one before."""
    max_wait: timedelta = timedelta(seconds=30)
    """No wait is longer: a step that needs more is a run to continue later."""
    on: frozenset[exceptions.FailureKind] = RETRYABLE | {exceptions.FailureKind.CONFLICT}
    """The kinds of failure retried — unavailable, in progress, exhausted, a conflict."""

    def wait_before(self, attempt: int, declared: timedelta | None) -> timedelta:
        """The wait before `attempt` + 1: what the error declared — a second when it says
        nothing — grown by `backoff` each attempt, never above `max_wait`."""
        first = declared if declared is not None else timedelta(seconds=1)
        return min(first * self.backoff ** (attempt - 1), self.max_wait)


class PipelineDeclaration:
    """What a pipeline declares, as data — the base of `Pipeline`, read by the bus and by the
    recorder, so neither needs the code that runs the steps."""

    steps: ClassVar[Sequence[type | Await] | Mapping[Any, Sequence[type | Await]]] = ()
    """The Commands it runs, in order — and the decisions it waits for (`Await`): a list, or a
    mapping of recipes picked by `by`."""
    by: ClassVar[str | None] = None
    """The field of the input that picks the recipe, when `steps` is a mapping."""
    always: ClassVar[type | None] = None
    """A Command that runs last whatever happened. It may take `ok` and `failure`."""
    resume: ClassVar[Resume] = Resume.RESTART
    owned_by: ClassVar[Sequence[str]] = Owner.BY_TENANT
    """The keys of the context a run belongs to — the tenant's by default; `(*Owner.BY_TENANT,
    "user_id")` a user's, `()` nobody's: one run of a `run_id` for everybody."""
    records: ClassVar[bool] = True
    """Whether the bus attaches the recorder that writes the run."""
    retry: ClassVar[Retry | None] = None
    """How a step that failed in a way worth running again is run again — never, by default."""
    lease: ClassVar[timedelta | None] = None
    """How long a run lives without its worker renewing it: one run of a `run_id` at a time
    across every replica, and a worker that died leaves its run `abandoned` — none by default."""

    @classmethod
    def recipes(cls) -> list[Sequence[type | Await]]:
        """Every recipe it has, in the order declared."""
        if isinstance(cls.steps, Mapping):
            return list(cls.steps.values())
        return [cls.steps]

    @classmethod
    def recipe_of(cls, dto: Any) -> list[type | Await]:
        """The steps `dto` runs, in order — the list, or the recipe its `by` field picks.

            steps = [Reserve, Charge]                         ->  [Reserve, Charge], always
            steps = {"card": [Reserve, Charge], "cash": [Reserve]}, by = "method"
            recipe_of(CommandCheckout(method="cash"))         ->  [Reserve]
            recipe_of(CommandCheckout(method="crypto"))       ->  ProgrammingError, no recipe

        A recipe per value with no `by` declared is `ProgrammingError` too."""
        if not isinstance(cls.steps, Mapping):
            return list(cls.steps)
        if cls.by is None:
            raise exceptions.ProgrammingError(
                f"{cls.__name__} has a recipe per value and says nothing of which field picks it: "
                'declare `by = "<field of the input>"`'
            )
        picked = getattr(dto, cls.by)
        if picked not in cls.steps:
            raise exceptions.ProgrammingError(
                f"{cls.__name__} has no recipe for {cls.by}={picked!r}: it has "
                f"{', '.join(repr(one) for one in cls.steps)}"
            )
        return list(cls.steps[picked])

    @classmethod
    def commands(cls) -> list[type]:
        """Every Command its recipes name, in the order declared, `always` last — repeated as
        the recipes repeat them; the `Await`s left out.

            steps = {"a": [Collect, Await("ok"), Bill], "b": [Collect]}, always = Close
            ->  [Collect, Bill, Collect, Close]
        """
        named = [
            step for recipe in cls.recipes() for step in recipe if isinstance(step, type)
        ]
        return named if cls.always is None else [*named, cls.always]

    @classmethod
    def recorded(cls, command: type, answered: Collection[type]) -> tuple[type, ...]:
        """What the recorder of this pipeline wraps: its own `command`, then each step this
        bus answers, once — an interceptor cannot wrap what the bus does not answer.

            recorded(CommandRunNightly, {CommandCollect, CommandBill})
            ->  (CommandRunNightly, CommandCollect, CommandBill)     CommandNotify unanswered
        """
        wrapped: list[type] = [command]
        for step in cls.commands():
            if step in answered and step not in wrapped:
                wrapped.append(step)
        return tuple(wrapped)

    @classmethod
    def problems(cls, command: type, answered: Collection[type], bus: str) -> list[str]:
        """What a bus named `bus`, answering `answered`, would miss when it runs this pipeline
        for `command` — said, never refused.

            problems(CommandRunNightly, {CommandCollect}, "billing")
            ->  ["pipeline RunNightly: step CommandBill is not answered by 'billing': …"]

        1. An `Await` whose field `command` lacks: the run could never be run again with it.
        2. Each step the bus does not answer: the run fails when it reaches it.
        3. Each required field of a step that `command` does not carry — `ok` and `failure`
           are given to `always`.
        Final: the messages, in the order the recipes declare what they are about."""
        given = getattr(command, "model_fields", {})
        found = [
            f"pipeline {cls.__name__}: it waits for '{waits.name}', which "
            f"{command.__name__} has no field to be run again with"
            for recipe in cls.recipes()
            for waits in recipe
            if isinstance(waits, Await) and waits.name not in given
        ]
        for step in cls.commands():
            if step not in answered:
                found.append(
                    f"pipeline {cls.__name__}: step {step.__name__} is not answered by "
                    f"'{bus}': it fails when the run reaches it"
                )
            taken = {*given, *(ENDING if step is cls.always else ())}
            found.extend(
                f"pipeline {cls.__name__}: step {step.__name__} needs '{field}', "
                f"which {command.__name__} does not have"
                for field, info in getattr(step, "model_fields", {}).items()
                if info.is_required() and field not in taken
            )
        return found
