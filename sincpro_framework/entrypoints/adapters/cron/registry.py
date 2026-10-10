"""Crons: one registry per bounded context, one class per cron, its dependencies injected.

    cron_payments = Crons[CronDependencyContextType]("cron-payments")
    cron_payments.add_dependency("cybersource", cybersource)

    @cron_payments.cron("0 2 * * *", timezone="America/La_Paz")
    class Reconcile(Cron):          # the bounded context's base: Cron + CronDependencyContextType
        def run(self, tick: Tick) -> None:
            self.cybersource(...)

Context: a cron is not a use case — it is a caller of use cases, like an RPC method or an MCP
tool, so it lives beside the buses and not on one. The registry has the shape of a bus on
purpose: a name that is its identity in logs and traces, dependencies by name, a decorator that
registers, and nothing accepted once it is built.
"""

import inspect
from collections.abc import Callable
from datetime import timedelta
from typing import Any, cast

from sincpro_log.logger import LoggerProxy, create_logger

from sincpro_framework.common.ordering import name_of
from sincpro_framework.context.domain.level import EntrypointKind
from sincpro_framework.context.entrypoint.facade import carrying
from sincpro_framework.ddd.events import DomainEvent
from sincpro_framework.ddd.repositories import IRepository
from sincpro_framework.dependencies import DependencyLocator
from sincpro_framework.entrypoints.adapters.cron.domain import (
    Cron,
    CronDefinition,
    CronExpression,
    Every,
    Missed,
    Overlap,
    RunOutcome,
    Tick,
    Trigger,
)
from sincpro_framework.event_driven import DeliveryFailurePolicy, EventRelay, Publisher
from sincpro_framework.exceptions import (
    BusAlreadyBuilt,
    DependencyAlreadyRegistered,
    DependencyNotRegistered,
)
from sincpro_framework.observability import Observability


def _declared_dependencies(cron_class: type[Cron]) -> set[str]:
    """Every attribute the cron and its bases annotate — the bounded context's
    `CronDependencyContextType` included."""
    return {name for klass in cron_class.__mro__ for name in inspect.get_annotations(klass)}


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


class Crons[TDeps]:
    """Parameterize with the bounded context's `CronDependencyContextType` so `deps` is typed,
    the way a bus is `UseFramework[DependencyContextType]`."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.logger: LoggerProxy = create_logger(name)
        self.observability = Observability(name)
        self._dependencies: dict[str, Any] = {}
        self._deps_locator = DependencyLocator(self._dependencies)
        self._definitions: dict[str, CronDefinition] = {}
        self._instances: dict[str, Cron] = {}
        self._replaced: dict[type[Cron], type[Cron]] = {}
        self._built = False

    def _latest(self, cron_class: type[Cron]) -> type[Cron]:
        """The class in force where `cron_class` was registered — itself, or whatever replaced
        it last."""
        while cron_class in self._replaced:
            cron_class = self._replaced[cron_class]
        return cron_class

    def _registered(self, cron_class: type[Cron]) -> CronDefinition | None:
        for definition in self._definitions.values():
            if definition.cron_class is cron_class:
                return definition
        return None

    def _replacing(self, cron_class: type[Cron], replaces: type[Cron], own_name: str) -> str:
        """The name `cron_class` runs under when it replaces `replaces`.

        1. `replaces` already replaced: it replaces the latest, with a warning.
        2. Nothing registered to replace: it is a cron of its own, with a warning.
        3. Final: the replaced cron's name, so its record of runs goes on.
        """
        latest = self._latest(replaces)
        if latest is not replaces:
            self.logger.warning(
                f"{name_of(cron_class)} replaces {name_of(replaces)}, which {name_of(latest)} "
                f"already replaced — it replaces {name_of(latest)}"
            )
        replaced = self._registered(latest)
        if replaced is None:
            self.logger.warning(
                f"{name_of(cron_class)} replaces {name_of(latest)}, which is not registered on "
                f"'{self.name}' — it runs as a cron of its own"
            )
            return own_name
        self._replaced[latest] = cron_class
        return replaced.name

    def add_dependency(self, name: str, dependency: Any) -> None:
        if self._built:
            raise BusAlreadyBuilt(
                f"dependency {name} registered late: '{self.name}' is already built, so it would "
                "never be used — register it before the gateway starts"
            )
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
        replaces: type[Cron] | None = None,
    ) -> Callable[[C], C]:
        """Register the decorated class to run at every tick of `expression` in `timezone`,
        or `every` interval. Its name — in runs, logs and status — is `<registry>.<Class>`
        unless `name` is given.

        Context: with `overlap=SKIP`, a run still unfinished `stale_after` past its tick is
        presumed dead — its replica crashed — and stops holding the cron. `replaces` runs the
        decorated class instead of a registered one, under that one's name, so its record of
        runs goes on; the schedule and the policies are the ones given here."""
        trigger = _trigger(expression, timezone, every)

        def register(cron_class: C) -> C:
            if self._built:
                raise BusAlreadyBuilt(
                    f"cron {cron_class.__name__} registered late: '{self.name}' is already built, so it would "
                    "never be used — register it before the gateway starts"
                )
            full_name = name or f"{self.name}.{cron_class.__name__}"
            if replaces is not None:
                full_name = self._replacing(cron_class, replaces, full_name)
            elif full_name in self._definitions:
                raise ValueError(f"cron {full_name} is already registered on '{self.name}'")
            self._definitions[full_name] = CronDefinition(
                full_name, cron_class, trigger, overlap, missed, missed_window, stale_after
            )
            return cron_class

        return register

    def run_relay(
        self,
        relay: EventRelay,
        every: timedelta = timedelta(seconds=2),
        name: str | None = None,
    ) -> EventRelay:
        """Runs `relay.run_once()` at every tick of `every` — a cron like any other, with its
        record of runs and its overlap policy.

            billing_crons.run_relay(relay, every=timedelta(seconds=5))

        Its name, in runs and logs, is `<registry>.relay` unless `name` is given.
        """

        def run(cron: Cron, tick: Tick) -> None:
            relay.run_once()

        driven = type(
            "RunEventRelay",
            (Cron,),
            {"run": run, "__doc__": f"One pass of the relay of {relay.source.__name__}."},
        )
        self.cron(every=every, name=name or f"{self.name}.relay")(driven)
        return relay

    def relay_deliverable_events(
        self,
        repository: IRepository,
        source: type[DomainEvent],
        to: Publisher,
        on_failure: DeliveryFailurePolicy | None = None,
        every: timedelta = timedelta(seconds=2),
        name: str | None = None,
    ) -> EventRelay:
        """Delivers every `DeliverableEventMixin` event of `source` the repository keeps to `to`
        — a `Publisher` over the broker — at every tick of `every`.

            billing_crons.relay_deliverable_events(
                repository=billing_repository,
                source=BillingDomainEvent,
                to=Publisher(FastStreamQueue(kafka)),
            )

        Hands the relay back, to drive by hand or look at.
        """
        relay = EventRelay(repository, source, to, on_failure)
        return self.run_relay(relay, every, name)

    def without(self, cron_class: type[Cron]) -> None:
        """Switch a registered cron off — the one in force where it was registered: it never
        runs, and no status shows it. One not registered is a warning."""
        if self._built:
            raise BusAlreadyBuilt(
                f"switching off {cron_class.__name__} registered late: '{self.name}' is already built, so it would "
                "never be used — register it before the gateway starts"
            )
        registered = self._registered(self._latest(cron_class))
        if registered is None:
            self.logger.warning(
                f"{name_of(cron_class)} is switched off, but it is not registered on "
                f"'{self.name}'"
            )
            return
        del self._definitions[registered.name]

    @property
    def deps(self) -> TDeps:
        """The dependencies registered with `add_dependency`; inside a cron, `self.<name>`."""
        return cast(TDeps, self._deps_locator)

    @property
    def definitions(self) -> tuple[CronDefinition, ...]:
        return tuple(self._definitions.values())

    def build(self) -> None:
        """Build one instance per cron with every dependency of the registry, as a bus does for
        its Features — refused when a cron declares one the registry does not have. Idempotent.
        """
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
            for dependency, value in self._dependencies.items():
                setattr(instance, dependency, value)
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
        with (
            self.observability.execution() as outermost,
            carrying(context, EntrypointKind.CRON),
        ):
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
