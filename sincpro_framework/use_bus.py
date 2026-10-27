"""`UseFramework`: one bounded context's bus — what a project registers its use cases on,
wires its dependencies into, and calls.

    billing = UseFramework[BillingDeps]("billing")
    billing.add_dependency("invoices", InvoiceRepository(session))

    @billing.feature(CommandIssueInvoice)
    class IssueInvoice(Feature): ...

    billing(CommandIssueInvoice(total=100))                       built on the first call
    with billing.context({"tenant_id": "acme"}):                  a scope for the block
        billing(CommandIssueInvoice(total=100), ResponseInvoice)

**Registrations are stored, the bus is built from them.** Every registration is stored as a call
that repeats it, so a registration after the build asks for a new *generation* — new buses,
new handler instances — built on the next call; a request already running finishes on the
generation it started on. `fresh()` replays them all on a new, unbuilt bus.

The other worlds a bus offers — context, interceptors, incomes, outcomes, pipelines, error
handlers, observability, hosting, cache and idempotency — are mixins, each in its own package.
"""

import threading
from typing import (
    Any,
    Callable,
    Dict,
    Generic,
    Iterable,
    Mapping,
    Optional,
    Type,
    cast,
)

from sincpro_log.logger import LoggerProxy, create_logger

from sincpro_framework import exceptions

from . import ioc
from .aio import AsyncBus
from .bus import FrameworkBus
from .bus_pipeline.incomes.mixins.bus_incomes import BusIncomesMixin
from .bus_pipeline.interceptors.mixins.bus_interceptors import BusInterceptorsMixin
from .bus_pipeline.outcomes.mixins.bus_outcomes import BusOutcomesMixin
from .common.serialization import read_values, rebuilt
from .context import context_executions
from .context.entrypoint.bus_context import BusContextMixin
from .context.entrypoint.bus_store import BusStoreMixin
from .data_layer.caching.entrypoint.bus_caching import BusCachingMixin
from .dependencies import DependencyLocator, TDeps
from .error_handler import BusErrorHandlersMixin
from .observability import Observability
from .observability.correlation import declare_metric_labels
from .observability.mixins.bus_observability import BusObservabilityMixin
from .pipelines.entrypoint.bus_pipelines import BusPipelinesMixin
from .remote_execution.entrypoint.hosted_context import HostedContextMixin
from .sincpro_abstractions import TypeDTO, TypeDTOResponse


class UseFramework(
    BusContextMixin,
    BusInterceptorsMixin,
    BusIncomesMixin,
    BusOutcomesMixin,
    BusPipelinesMixin,
    BusErrorHandlersMixin,
    BusObservabilityMixin,
    HostedContextMixin,
    BusStoreMixin,
    BusCachingMixin,
    Generic[TDeps],
):
    """One bounded context's bus — where its use cases are registered, its dependencies
    wired, and it is called.

    Parameterize with the bounded context's ``DependencyContextType`` so registered
    adapters are typed on ``framework.deps``::

        framework = UseFramework[DependencyContextType]("my-context")
        framework.add_dependency("my_adapter", MyAdapter())
        framework.deps.my_adapter  # MyAdapter, same name Features get as self.my_adapter

    Each world it offers lives in its own component and is mixed in here: the context
    (`BusContextMixin`), interceptors (`BusInterceptorsMixin`), incomes (`BusIncomesMixin`),
    outcomes (`BusOutcomesMixin`), pipelines (`BusPipelinesMixin`), error handlers
    (`BusErrorHandlersMixin`), traces (`BusObservabilityMixin`), where the context is hosted
    (`HostedContextMixin`) where it saves what it saves (`BusStoreMixin`) and its cache and idempotency (`BusCachingMixin`). What stays here is
    the bus itself: registering, building and executing.
    """

    _name: str

    def __init__(
        self,
        bundled_context_name: str = "sincpro_framework",
        log_after_execution: bool = True,
        log_app_services: bool = True,
        log_features: bool = True,
        package: Optional[str] = None,
        hide_in_logs: Iterable[str] = (),
        metric_labels: Iterable[Any] = (),
    ):
        """A bus for the bounded context `bundled_context_name` — not built yet: the first
        call, or `build_root_bus()`, builds it.

            UseFramework("billing", hide_in_logs=["TOKEN"], metric_labels=["company"])

        Context: the name is the bounded context's identity everywhere — its logger, its
        GlitchTip app, its node of the context, the prefix of its use cases' names.
        - `log_after_execution=False` silences every execution log line; `log_app_services`
          and `log_features` silence one kind only.
        - `package` is the Poetry distribution named in the Sentry release and the OTel
          `service.name` — detected from the caller outside `sincpro_framework` when omitted.
        - `hide_in_logs` removes context keys from every signal — log lines, spans, metrics and
          GlitchTip events; everything else the context says goes on all of them.
        - `metric_labels` puts context keys on every metric series, beside the release,
          service, version and tenant every series carries — a key (`"company"`) or a field
          reference (`field_path(BillingContext).company`). Declared here, before the build,
          because a backend fixes a series' labels when it creates it.

        1. The settings stored as given, so `fresh()` builds a bus with the same ones.
        2. Logger, observability, context, incomes and outcomes, each component's own state.
        3. The container the buses and handlers come from, the dependency registry, error
           handlers and interceptors.
        Final: an unbuilt bus, its hosting read from the context map.
        """
        self._settings = (
            bundled_context_name,
            log_after_execution,
            log_app_services,
            log_features,
            package,
            tuple(hide_in_logs),
            tuple(metric_labels),
        )
        self._name: str = bundled_context_name
        """The bounded context's name: defined here, once. Everything else — its logger, its
        signals, its node of the context, the identity of its use cases — reads it through `name`."""
        self._registrations: list[Callable[[UseFramework], Any]] = []
        """Every registration, in order, as a call that repeats it on another bus — `fresh`."""

        # Logger
        self._is_logger_configured: bool = False
        self._logger: LoggerProxy | None = None
        self.log_after_execution: bool = log_after_execution
        self.log_app_services: bool = log_app_services
        self.log_features: bool = log_features
        self.observability = Observability(self.name, package=package or "")

        self._init_context_storage()
        self._init_incomes()
        self._init_outcomes()
        self.hidden_in_logs: frozenset[str] = frozenset(hide_in_logs)
        declare_metric_labels(metric_labels)
        # Who the lines come from first, the execution's context after: a key the application
        # sets itself (its own `tenant`) wins over the deployment's.
        self.logger.add_context_source(self.observability.log_identity)
        self.logger.add_context_source(self._context_for_logs)

        # Container
        self._sp_container = ioc.FrameworkContainer(  # type: ignore[call-arg]
            logger_bus=self.logger,
            observability=self.observability,
            incomes=self._incomes,
            outcomes=self._outcomes,
        )
        self._sp_container.logger_bus = self.logger  # type: ignore[assignment]

        # Registry for dynamic dep injection
        self.dynamic_dep_registry: Dict[str, Any] = dict()
        self._deps_locator = DependencyLocator(self.dynamic_dep_registry)

        self._init_error_handlers()
        self._init_interceptors()
        self._execution_ids: Callable[[], str] | None = None

        self.was_initialized: bool = False
        self._build_lock = threading.RLock()
        self.bus: FrameworkBus | None = None
        self._revision: int = 0
        """How many registrations reached this bus after it was built — each asks for a new
        generation."""
        self._built_revision: int = -1
        """The revision the generation answering was built from."""
        self._extending: bool = False
        """While an extension runs: what it registers asks for one generation, all together."""

        self._init_hosting(bundled_context_name)

    @property
    def name(self) -> str:
        """The bounded context's name — its logger, its GlitchTip app, its OTel service."""
        return self._name

    @property
    def revision(self) -> int:
        """How many registrations reached this bus after it was built. Whatever holds a snapshot of
        the bus — a catalog of its use cases — compares it to know the snapshot is still true.
        """
        return self._revision

    @property
    def logger(self) -> LoggerProxy:
        """The bounded context's logger — created on first use, named after the bus."""
        if not self._is_logger_configured:
            self._logger = create_logger(self.name)
            self._is_logger_configured = True
        return self._logger  # type: ignore[return-value]

    def _registered(self, replay: Callable[["UseFramework[TDeps]"], Any], what: str) -> None:
        """Store `replay` to repeat this registration on a generation `fresh()` makes; and when the
        bus is already built, ask for a new generation — the next execution builds it.

        Context: called under `_build_lock`, with the registration already made, so a build never
        reads half of one. Inside an extension, the extension asks once for everything it
        registered.
        """
        with self._build_lock:
            self._registrations.append(replay)
            if not self.was_initialized or self._extending:
                return
            self._revision += 1
            self.logger.info(
                f"{what} registered after '{self.name}' was built: it answers from the next "
                "execution, on a new generation of the bus"
            )

    def feature(
        self, dto: ioc.DTORegistration, replaces: type | None = None
    ) -> Callable[[ioc.T], ioc.T]:
        """Register the decorated class as the Feature that answers `dto` (or each DTO listed).

            @billing.feature(CommandComputeTax, replaces=ComputeTax)

        Context: `replaces` names the Feature registered now; that one is skipped from then on,
        and the build log and introspection say who replaced it. Registered after the bus was
        built, it answers from the next execution.
        """
        register = ioc.inject_feature_to_bus(self._sp_container, dto, replaces, self.name)

        def registered(handler: ioc.T) -> ioc.T:
            """Register `handler` under the build lock, store how to repeat it, and answer it
            unchanged."""
            with self._build_lock:
                answer = register(handler)
                self._registered(
                    lambda bus: bus.feature(dto, replaces)(handler),
                    f"Feature {handler.__name__}",
                )
            return answer

        return registered

    def app_service(
        self, dto: ioc.DTORegistration, replaces: type | None = None
    ) -> Callable[[ioc.T], ioc.T]:
        """Register the decorated class as the ApplicationService that answers `dto`; with
        `replaces`, instead of the one registered now. Registered after the bus was built, it
        answers from the next execution.

            @billing.app_service(CommandCheckout)
            class Checkout(ApplicationService): ...      ->  Checkout, unchanged

            @billing.app_service(CommandCheckout, replaces=Checkout)
            class CheckoutWithFraudCheck(ApplicationService): ...

        The registration is stored (`_registered`), so a generation `fresh()` makes repeats it.
        """
        register = ioc.inject_app_service_to_bus(self._sp_container, dto, replaces, self.name)

        def registered(handler: ioc.T) -> ioc.T:
            """Register `handler` under the build lock, store how to repeat it, and answer it
            unchanged."""
            with self._build_lock:
                answer = register(handler)
                self._registered(
                    lambda bus: bus.app_service(dto, replaces)(handler),
                    f"ApplicationService {handler.__name__}",
                )
            return answer

        return registered

    def handler_of(self, dto: type) -> type | None:
        """The Feature or ApplicationService registered now for `dto` — what a `replaces=` names."""
        for registry in (
            self._sp_container.feature_registry.kwargs,
            self._sp_container.app_service_registry.kwargs,
        ):
            if dto in registry:
                return registry[dto].provides
        return None

    def replaced_for(self, dto: type) -> tuple[str, ...]:
        """`module.Class` of each handler of `dto` that `replaces=` took the place of, oldest
        first — empty when the one answering is the first registered."""
        return tuple(self._sp_container.replacements.kwargs.get(dto, ()))

    def feature_handlers(self) -> dict[type, type]:
        """Every DTO a Feature answers now → the Feature — read without building the bus."""
        registry = self._sp_container.feature_registry.kwargs
        return {dto: registered.provides for dto, registered in registry.items()}

    def app_service_handlers(self) -> dict[type, type]:
        """Every DTO an ApplicationService answers now → the ApplicationService — read without
        building the bus."""
        registry = self._sp_container.app_service_registry.kwargs
        return {dto: registered.provides for dto, registered in registry.items()}

    def handlers(self) -> dict[type, type]:
        """Every DTO a Feature or ApplicationService answers now → the class answering it —
        read without building the bus, so a check made at startup changes nothing."""
        return {**self.feature_handlers(), **self.app_service_handlers()}

    def extend(self, extension: Callable[["UseFramework[TDeps]"], None]) -> None:
        """Wire a component into this bus and into every generation `fresh()` makes of it —
        `extension(bus)` registers its interceptors, dependencies or handlers.

            billing.extend(access_control.attach)

        Context: what `extension` registers is not stored one by one; the extension is, so a
        `fresh()` bus runs it again against itself, and a component bound to the bus it was
        wired into — an interceptor that reads its bus — is bound to that one. After the bus was
        built, the extension answers from the next execution, whole: it runs under the build
        lock, so no execution builds a generation with half of it.
        """
        with self._build_lock:
            registered_before = len(self._registrations)
            outer, self._extending = self._extending, True
            try:
                extension(self)
            finally:
                self._extending = outer
                del self._registrations[registered_before:]
            self._registered(
                lambda bus: bus.extend(extension),
                f"extension {getattr(extension, '__qualname__', extension)}",
            )

    def fresh(self) -> "UseFramework[TDeps]":
        """A new bus of this context, not built: the same settings and observability, and every
        Feature, ApplicationService, dependency, interceptor and error handler registered on this
        one, again, in the order they were — what a new generation of the bus starts from.

            generation = billing.fresh()
            generation.feature(CommandQuote)(Quote)      # this one only
            generation.build_root_bus()

        Context: dependencies are the same objects, not copies — a connection is not opened
        twice. Observability is shared, so every generation reports as this bus does.
        """
        fresh: UseFramework[TDeps] = UseFramework(*self._settings)
        fresh._share_observability(self.observability)
        for registration in self._registrations:
            registration(fresh)
        return fresh

    def _share_observability(self, observability: Observability) -> None:
        """Report through `observability` — the bus `fresh()` copied from — on this bus and in
        its container, so every generation shares one exporter, one Sentry client, one set of
        metrics."""
        self.observability = observability
        self._sp_container.observability.override(observability)

    def _context_for_logs(self) -> Dict[str, Any]:
        """The execution's context, as fields on every log line this bus writes — its identity
        included, the execution's own over what the context was handed."""
        running = context_executions.current()
        context = {
            **self._get_context().copy(),
            **(running.chain() if running is not None else {}),
        }
        return {
            key: value
            for key, value in context.items()
            if isinstance(key, str) and key not in self.hidden_in_logs
        }

    def _add_dependencies_provided_by_user(self):
        """Every dependency added with `add_dependency` set as an attribute of every Feature
        and ApplicationService registered — what a handler reads as `self.<name>`."""
        if "feature_registry" in self._sp_container.feature_bus.attributes:
            feature_registry = self._sp_container.feature_bus.attributes[
                "feature_registry"
            ].kwargs

            for _, feature in feature_registry.items():
                feature.add_attributes(**self.dynamic_dep_registry)

        if "app_service_registry" in self._sp_container.app_service_bus.attributes:
            app_service_registry = self._sp_container.app_service_bus.attributes[
                "app_service_registry"
            ].kwargs

            for _, app_service in app_service_registry.items():
                app_service.add_attributes(**self.dynamic_dep_registry)

    def _create_buses_with_their_settings(self) -> None:
        """What each bus is created with, known only once every registration is in: the context it
        runs in (this bus, its providers, who mints the execution ids) and whether it logs each
        execution. A bus is never handed these afterwards.

        Context: the buses are created when `framework_bus()` is first called, after this."""
        context = {
            "context_owner": self,
            "context_providers": self._context_providers,
            "new_execution_id": self._execution_ids,
        }
        self._sp_container.feature_bus.add_kwargs(
            log_after_execution=self.log_after_execution and self.log_features, **context
        )
        self._sp_container.app_service_bus.add_kwargs(
            log_after_execution=self.log_after_execution and self.log_app_services, **context
        )
        self._sp_container.framework_bus.add_kwargs(
            log_after_execution=self.log_after_execution
        )

    def _assemble_handlers(self, bus: FrameworkBus) -> None:
        """Each handler run once per request when a `declare_for` names its Command, wrapped by
        its interceptors and the recorders of the pipelines, and who replaced whom — said once,
        in the build log."""
        self._idempotency_by_type(bus)
        self._wrap_handlers(bus, self._pipeline_recorders())
        feature_bus, app_service_bus = bus.feature_bus, bus.app_service_bus
        replacements = self._sp_container.replacements()
        for command, replaced in replacements.items():
            handler = (
                feature_bus.feature_registry.get(command)
                or app_service_bus.app_service_registry[command]
            )
            self.logger.info(
                f"{command.__name__} is handled by {type(handler).__name__} "
                f"(replaces {', '.join(name.rsplit('.', 1)[-1] for name in replaced)})"
            )
        feature_bus.replacements = dict(replacements)
        app_service_bus.replacements = dict(replacements)

    def _build_root_bus(self):
        """A generation of this bus: new buses and new handler instances, built from every
        registration in now. It takes the place of the one answering; a request already running
        stays on the generation it started on, to the end.

        1. The two buses the container holds once are dropped, so this generation creates its
           own — and its own handlers — and nothing the running one holds is touched.
        2. Dependencies, error chains and the settings each bus is created with.
        3. The handlers wrapped by their interceptors and bound to this bus's context.
        Final: the generation answers from here; observability starts with the first one, and
        the process joins the process's store once one is set. A build that raises leaves the
        generation that was answering as it was.
        """
        self._sp_container.feature_bus.reset()
        self._sp_container.app_service_bus.reset()
        self._add_dependencies_provided_by_user()
        self._add_error_handlers_provided_by_user()
        for note in self._error_chain_notes():
            self.logger.warning(f"error handler: {note}")
        self._create_buses_with_their_settings()

        bus: FrameworkBus = self._sp_container.framework_bus()  # type: ignore[assignment]
        self._assemble_handlers(bus)
        self._bind_context_to_handlers(bus)

        # The container already injects this into the three buses; retained as a guard so
        # the guarantee does not depend on the provider still being the one the
        # framework_bus Factory captured.
        bus.observability = self.observability

        if self.bus is None:
            self.observability.start(self.logger)
        self._process_joined()
        self.bus = bus
        self._built_revision = self._revision
        # Last, so a thread that reads the flag without the lock never sees a bus half wired.
        self.was_initialized = True

    def build_root_bus(self):
        """Bring the bus up to date now: build it, or build a new generation when something was
        registered after the last one — nothing, for a reference to a context another service
        hosts (`is_reference`).

        Context: the first execution does it by itself. Called on purpose, it surfaces a wiring
        error at that line — an entrypoint mounting the bus, a warm-up — instead of at the first
        request. Under a lock: two threads that reach a bus out of date at the same time, as an
        async fan-out or an event fan-out does, build one generation, never two.
        """
        with self._build_lock:
            if self.is_reference or (
                self.bus is not None and self._built_revision == self._revision
            ):
                return
            self._build_root_bus()

    def add_dependency(self, name, dep: Any, replaces: Any = None):
        """Register `dep` under `name`: every Feature and ApplicationService reads it as
        `self.<name>`, and the bounded context's root as `bus.deps.<name>`.

            billing.add_dependency("cache", RedisCache(...), replaces=memory_cache)

        Context: `replaces` names the object registered under `name` now, and `dep` takes its
        place; a second one under a name already taken, without it, is refused naming what is
        there. Replacing something that is not what is registered — or nothing — works, with a
        warning. Added after the bus was built, it reaches the handlers from the next execution.
        """
        with self._build_lock:
            registered = self.dynamic_dep_registry.get(name)
            if name in self.dynamic_dep_registry and replaces is None:
                error = exceptions.DependencyAlreadyRegistered(
                    f"The dependency {name} is already injected "
                    f"({type(registered).__module__}.{type(registered).__qualname__}): to put "
                    f"another in its place, add_dependency({name!r}, ..., replaces=<the one "
                    "registered>)"
                )
                self.observability.record_error(error, "", "framework", kind="framework")
                raise error
            if replaces is not None and replaces is not registered:
                self.logger.warning(
                    f"dependency {name}: replaces {type(replaces).__qualname__}, but "
                    + (
                        f"{type(registered).__qualname__} is registered"
                        if name in self.dynamic_dep_registry
                        else "nothing is registered"
                    )
                    + f" — {type(dep).__qualname__} takes the name"
                )
            self.dynamic_dep_registry[name] = dep
            self._registered(
                lambda bus: bus.add_dependency(name, dep, replaces), f"dependency {name}"
            )

    @property
    def deps(self) -> TDeps:
        """Read-only locator of dependencies registered with ``add_dependency``.

        Inside a Feature / ApplicationService still use ``self.<name>``.
        Use this from the bounded-context root (SDK caller, test, entrypoint)::

            adapter = framework.deps.my_adapter
        """
        return cast(TDeps, self._deps_locator)

    @property
    def dto_registry(self) -> Mapping[str, type]:
        """Every message this bus answers, by its identity (`registered_name`) — an event by its
        `name`, a use case by `context.Class` — mapped to its class. Read from the registrations,
        so it builds nothing: a reference answers it as the service hosting it does.

        A live query, not a snapshot: it always answers what is registered at the moment it's
        asked, not what was registered when this property was first read.
        """
        return self._sp_container.dto_registry.kwargs

    def map_to_dto_or_event(
        self, name: str, payload: "str | bytes | Mapping[str, Any]"
    ) -> Any:
        """The DTO or event registered under `name` — its identity, or within this bus its class
        name alone — rebuilt from its JSON values (`message.rebuilt`).

        What a consumer has once a message arrives off the wire — another service, Kafka,
        RabbitMQ, this framework's own `BackgroundQueue` — ready to hand to ``bus(...)``.

            billing.map_to_dto_or_event("billing.CommandIssueInvoice", '{"total": 10}')
            ->  CommandIssueInvoice(total=10)
            billing.map_to_dto_or_event("CommandIssueInvoice", {"total": 10})   ->  the same
            billing.map_to_dto_or_event("CommandNope", {})    ->  UnknownDTOToExecute

        The full identity is tried first, then `<bus>.<name>`.
        """
        registry = self.dto_registry
        dto_type = registry.get(name) or registry.get(f"{self.name}.{name}")
        if dto_type is None:
            raise exceptions.UnknownDTOToExecute(f"no DTO or event registered under [{name}]")
        return rebuilt(read_values(payload), dto_type)

    def execution_ids(self, generator: Callable[[], str]) -> None:
        """Mint the `execution_id` of every execution of this bus with `generator` instead of a
        UUID v7 — a ULID, a UUID v4, a Snowflake id. Given after the bus was built, it mints from
        the next execution.

            billing.execution_ids(lambda: str(ulid.new()))

        Context: an id given from outside — `bus.context({"execution_id": …})`, a header — still
        wins for a root execution; the generator only mints the ones nobody gave.
        """
        if not callable(generator):
            raise TypeError(
                f"execution_ids takes a function that returns a str, not {generator!r}"
            )
        with self._build_lock:
            self._execution_ids = generator
            self._registered(
                lambda bus: bus.execution_ids(generator),
                f"execution id generator {getattr(generator, '__qualname__', generator)}",
            )

    def get_async_bus(self) -> AsyncBus:
        """Return a stateless async facade over this framework's bus.

        Each call runs the bus's own call on a worker thread, so the bus is built on first use
        (same lazy behavior as ``__call__``) and a reference forwards. Use this from a caller
        that is itself ``async def`` and wants to fan out several DTOs concurrently, e.g. via
        ``asyncio.gather``, without blocking its event loop. See ``Bus.get_async_bus`` /
        ``AsyncBus`` for the propagation and reuse semantics.
        """
        return AsyncBus(self)

    @property
    def is_ready(self) -> bool:
        """Whether this bus can answer now — built, or a reference, which has nothing to build."""
        return self.is_reference or (self.was_initialized and self.bus is not None)

    def __call__(
        self, dto: TypeDTO, return_type: Type[TypeDTOResponse] | None = None
    ) -> TypeDTOResponse | None:
        """Execute `dto` and answer what its use case answered — here, or on the service
        hosting this context when this bus is a reference to it (`is_reference`).

            billing(CommandIssueInvoice(total=100))                    the response, or None
            billing(CommandIssueInvoice(total=100), ResponseInvoice)   typed, for the IDE

        1. A reference forwards the call to where the context is hosted.
        2. A bus not built, or with registrations after its build, builds its generation now.
        3. The generation is read once: the whole execution runs on the one answering when it
           starts.
        Final: executed inside the scope already open on this bus, or a new scope of its own —
        an entrypoint when nothing opened the flow.
        """
        if self.is_reference:
            return self._executed_where_hosted(dto, return_type)
        if self.bus is None or self._built_revision != self._revision:
            self.build_root_bus()

        # Read once: the whole execution runs on the generation answering when it starts.
        bus = self.bus
        if bus is None:
            error = exceptions.SincproFrameworkNotBuilt(
                "Check the decorators are rigistering the features and app services, check the imports of each "
                "feature and app service"
            )
            self.observability.record_error(error, "", "framework", kind="framework")
            raise error

        if self._entered_here():
            return bus.execute(dto)
        with self._scope({}):
            return bus.execute(dto)
