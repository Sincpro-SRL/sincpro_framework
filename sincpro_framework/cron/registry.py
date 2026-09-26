"""Crons: one registry per bounded context, one class per cron, its dependencies injected.

    cron_payments = Crons("cron-payments")
    cron_payments.add_dependency("cybersource", cybersource)

    @cron_payments.cron("0 2 * * *", timezone="America/La_Paz")
    class Reconcile(Cron):
        cybersource: UseFramework

        def run(self, tick: Tick) -> None:
            ...

Context: a cron is not a use case — it is a caller of use cases, like an RPC method or an MCP
tool, so it lives beside the buses and not on one. The registry has the shape of a bus on
purpose: a name that is its identity in logs and traces, dependencies by name, a decorator that
registers, and nothing accepted once it is built.
"""

import inspect
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from sincpro_log.logger import LoggerProxy, create_logger

from sincpro_framework.context.mixin import carrying
from sincpro_framework.cron.runs import CronRuns, RunOutcome
from sincpro_framework.cron.triggers import CronExpression, Every, Trigger
from sincpro_framework.exceptions import (
    BusAlreadyBuilt,
    DependencyAlreadyRegistered,
    DependencyNotRegistered,
)
from sincpro_framework.observability import Observability


class Overlap(StrEnum):
    """What a tick does while the previous run of its cron has not finished."""

    SKIP = "skip"
    ALLOW = "allow"


class Missed(StrEnum):
    """What happens to ticks that passed while nothing ran — a deploy, an outage."""

    SKIP = "skip"
    RUN_LATEST = "run_latest"
    RUN_ALL = "run_all"


@dataclass(frozen=True)
class Tick:
    """The run in progress: which cron, and the tick it answers."""

    cron: str
    scheduled_for: datetime
    runs: CronRuns = field(repr=False, compare=False)

    def once(self, key: str) -> bool:
        """Context: `True` the first time `key` is asked for this tick, on any replica; `False`
        after — a step that must not be repeated when the tick is. At most once: a step that
        fails after `once` is not retried by the next run of the same tick."""
        return self.runs.claim(self.cron, self.scheduled_for, key)


class Cron(ABC):
    """A cron: its dependencies are annotated attributes, injected by its registry; `run` is
    what happens at each tick. One instance serves every tick — state lives in locals."""

    @abstractmethod
    def run(self, tick: Tick) -> None: ...


@dataclass(frozen=True)
class CronDefinition:
    name: str
    cron_class: type[Cron]
    trigger: Trigger
    overlap: Overlap
    missed: Missed
    missed_window: timedelta
    stale_after: timedelta


def _declared_dependencies(cron: type[Cron]) -> set[str]:
    return {
        name
        for klass in cron.__mro__
        if issubclass(klass, Cron) and klass is not Cron
        for name in inspect.get_annotations(klass)
    }


def _trigger(
    expression: str | None, timezone: str | None, every: timedelta | None
) -> Trigger:
    if every is not None and expression is None:
        return Every(every)
    if expression is None or every is not None:
        raise ValueError("a cron takes one of an expression or every=")
    if timezone is None:
        raise ValueError(f"cron {expression!r} needs a timezone: 02:00 means 02:00 somewhere")
    return CronExpression(expression, timezone)


class Crons:
    def __init__(self, name: str) -> None:
        self.name = name
        self.logger: LoggerProxy = create_logger(name)
        self.observability = Observability(name)
        self._dependencies: dict[str, Any] = {}
        self._definitions: dict[str, CronDefinition] = {}
        self._instances: dict[str, Cron] = {}
        self._built = False

    def _refuse_when_built(self, what: str) -> None:
        if self._built:
            raise BusAlreadyBuilt(
                f"{what} registered late: '{self.name}' is already built, so it would never be "
                "used — register it before the gateway starts"
            )

    def add_dependency(self, name: str, dependency: Any) -> None:
        self._refuse_when_built(f"dependency {name}")
        if name in self._dependencies:
            raise DependencyAlreadyRegistered(
                f"'{self.name}' already has a dependency {name}"
            )
        self._dependencies[name] = dependency

    def cron[C: type[Cron]](
        self,
        expression: str | None = None,
        timezone: str | None = None,
        every: timedelta | None = None,
        name: str | None = None,
        overlap: Overlap = Overlap.SKIP,
        missed: Missed = Missed.RUN_LATEST,
        missed_window: timedelta = timedelta(days=1),
        stale_after: timedelta = timedelta(hours=1),
    ) -> Callable[[C], C]:
        """Register the decorated class to run at every tick of `expression` in `timezone`,
        or `every` interval. Its name — in runs, logs and status — is `<registry>.<Class>`
        unless `name` is given.

        Context: with `overlap=SKIP`, a run still unfinished `stale_after` past its tick is
        presumed dead — its replica crashed — and stops holding the cron."""
        trigger = _trigger(expression, timezone, every)

        def register(cron_class: C) -> C:
            self._refuse_when_built(f"cron {cron_class.__name__}")
            full_name = name or f"{self.name}.{cron_class.__name__}"
            if full_name in self._definitions:
                raise ValueError(f"cron {full_name} is already registered on '{self.name}'")
            self._definitions[full_name] = CronDefinition(
                full_name, cron_class, trigger, overlap, missed, missed_window, stale_after
            )
            return cron_class

        return register

    @property
    def definitions(self) -> tuple[CronDefinition, ...]:
        return tuple(self._definitions.values())

    def build(self) -> None:
        """Build one instance per cron with its dependencies — refused when a cron declares one
        the registry does not have. Idempotent."""
        if self._built:
            return
        for definition in self._definitions.values():
            declared = _declared_dependencies(definition.cron_class)
            missing = sorted(declared - set(self._dependencies))
            if missing:
                raise DependencyNotRegistered(
                    f"{definition.cron_class.__name__} needs {', '.join(missing)}, which "
                    f"'{self.name}' does not have — add_dependency it before the gateway starts"
                )
            instance = definition.cron_class()
            for dependency in declared:
                setattr(instance, dependency, self._dependencies[dependency])
            self._instances[definition.name] = instance
        self.observability.start(self.logger)
        self._built = True

    def execute(self, definition: CronDefinition, tick: Tick) -> RunOutcome:
        """Context: a cron is the outermost caller, so it reports a failure once — including one
        that happened inside a bus it called, which then keeps its `failed_in`.

        1. Hand the tick to every bus the cron calls, as their context.
        2. Run it in its own span.
        3. Final: the outcome; a failure is reported, never raised to the clock.
        """
        instance = self._instances[definition.name]
        context = {"cron": definition.name, "scheduled_for": tick.scheduled_for.isoformat()}
        with self.observability.execution() as outermost, carrying(context):
            with self.observability.span(definition.name, "cron") as span:
                with self.observability.handling(definition.name):
                    try:
                        instance.run(tick)
                    except Exception as error:
                        self.observability.failed(error, tick, instance, "cron", span)
                        if outermost:
                            self.observability.escaped(error, tick)
                        return RunOutcome.FAILED
        self.logger.info(f"{definition.name} ran for {tick.scheduled_for.isoformat()}")
        return RunOutcome.SUCCEEDED
