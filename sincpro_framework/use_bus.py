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

from . import ioc
from .aio import AsyncBus
from .bus import FrameworkBus
from .bus_pipeline.interceptors.mixins.bus_interceptors import BusInterceptorsMixin
from .bus_pipeline.outcomes.mixins.bus_outcomes import BusOutcomesMixin
from .common.serialization import read_values, rebuilt
from .context.infrastructure.tree import current_execution
from .context.mixins.bus_context import BusContextMixin
from .dependencies import DependencyLocator, TDeps
from .error_handler import BusErrorHandlersMixin
from .exceptions import (
    BusAlreadyBuilt,
    DependencyAlreadyRegistered,
    SincproFrameworkNotBuilt,
    UnknownDTOToExecute,
)
from .observability import Observability
from .observability.correlation import declare_metric_labels
from .observability.mixins.bus_observability import BusObservabilityMixin
from .remote_execution.mixins.hosted_context import HostedContextMixin
from .sincpro_abstractions import TypeDTO, TypeDTOResponse


class UseFramework(
    BusContextMixin,
    BusInterceptorsMixin,
    BusOutcomesMixin,
    BusErrorHandlersMixin,
    BusObservabilityMixin,
    HostedContextMixin,
    Generic[TDeps],
):
    """Main class to use the framework, this is the main entry point to configure the framework.

    Parameterize with the bounded context's ``DependencyContextType`` so registered
    adapters are typed on ``framework.deps``::

        framework = UseFramework[DependencyContextType]("my-context")
        framework.add_dependency("my_adapter", MyAdapter())
        framework.deps.my_adapter  # MyAdapter, same name Features get as self.my_adapter

    Each world it offers lives in its own component and is mixed in here: the context
    (`BusContextMixin`), interceptors (`BusInterceptorsMixin`), outcomes (`BusOutcomesMixin`),
    error handlers (`BusErrorHandlersMixin`), traces (`BusObservabilityMixin`) and where the
    context is hosted (`HostedContextMixin`). What stays here is the bus itself: registering,
    building and executing.
    """

    _logger_name: str

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
        """Initialize the framework

        Args:
            bundled_context_name (str): Name of the logger / GlitchTip app
            log_after_execution (bool): Log after execution if False, All logs will be disabled
            log_app_services (bool): Log app services, If log_after_execution is False, this will be disabled
            log_features (bool): Log features, If log_after_execution is False, this will be disabled
            package: Optional Poetry distribution name used for Sentry
                release and OTel ``service.name``. When omitted, the
                caller outside ``sincpro_framework`` is detected.
            hide_in_logs: Context keys kept off every signal — log lines, spans, metrics and
                GlitchTip events (e.g. ``["TOKEN"]``). Everything else set with ``context()``
                goes on all of them (PRD_03 §4.10).
            metric_labels: Context keys on every metric series, beside the release, service,
                version and tenant every series carries — a key (``"company"``) or a field
                reference (``of(BillingContext).company``). Declared here, before the bus is
                built, because a backend fixes a series' labels when it creates it.
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
        self._registrations: list[Callable[[UseFramework], Any]] = []
        """Every registration, in order, as a call that repeats it on another bus — `fresh`."""

        # Logger
        self._is_logger_configured: bool = False
        self._logger_name: str = bundled_context_name
        self._logger: LoggerProxy | None = None
        self.log_after_execution: bool = log_after_execution
        self.log_app_services: bool = log_app_services
        self.log_features: bool = log_features
        self.observability = Observability(bundled_context_name, package=package or "")

        self._init_context_storage()
        self._hidden_in_logs: frozenset[str] = frozenset(hide_in_logs)
        declare_metric_labels(metric_labels)
        # Who the lines come from first, the execution's context after: a key the application
        # sets itself (its own `tenant`) wins over the deployment's.
        self.logger.add_context_source(self.observability.log_identity)
        self.logger.add_context_source(self._context_for_logs)

        # Container
        self._sp_container = ioc.FrameworkContainer(  # type: ignore[call-arg]
            logger_bus=self.logger, observability=self.observability
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

        self._init_hosting(bundled_context_name)

    def feature(
        self, dto: ioc.DTORegistration, replaces: type | None = None
    ) -> Callable[[ioc.T], ioc.T]:
        """Register the decorated class as the Feature that answers `dto` (or each DTO listed).

            @billing.feature(CommandComputeTax, replaces=ComputeTax)

        Context: `replaces` names the Feature registered now; that one is skipped from then on,
        and the build log and introspection say who replaced it.
        """
        if self.was_initialized:
            raise BusAlreadyBuilt(
                f"Feature for {', '.join(one.__name__ for one in (dto if isinstance(dto, list) else [dto]))} registered late: '{self._logger_name}' is already built, so it would "
                "never be used — register it before the first execution"
            )
        register = ioc.inject_feature_to_bus(self._sp_container, dto, replaces, self.name)

        def registered(handler: ioc.T) -> ioc.T:
            answer = register(handler)
            self._registrations.append(lambda bus: bus.feature(dto, replaces)(handler))
            return answer

        return registered

    def app_service(
        self, dto: ioc.DTORegistration, replaces: type | None = None
    ) -> Callable[[ioc.T], ioc.T]:
        """Register the decorated class as the ApplicationService that answers `dto`; with
        `replaces`, instead of the one registered now."""
        if self.was_initialized:
            raise BusAlreadyBuilt(
                f"ApplicationService for {', '.join(one.__name__ for one in (dto if isinstance(dto, list) else [dto]))} registered late: '{self._logger_name}' is already built, so it would "
                "never be used — register it before the first execution"
            )
        register = ioc.inject_app_service_to_bus(self._sp_container, dto, replaces, self.name)

        def registered(handler: ioc.T) -> ioc.T:
            answer = register(handler)
            self._registrations.append(lambda bus: bus.app_service(dto, replaces)(handler))
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

        Context: what `extension` registers is not kept one by one; the extension is, so a
        generation runs it again against itself. A component bound to the bus it was wired into
        — an interceptor that reads its bus — is then bound to the generation, not to this one.
        Refused once the bus is built, like what it registers.
        """
        if self.was_initialized:
            raise BusAlreadyBuilt(
                f"extension {getattr(extension, '__qualname__', extension)} registered late: '{self._logger_name}' is already built, so it would "
                "never be used — register it before the first execution"
            )
        kept = len(self._registrations)
        extension(self)
        del self._registrations[kept:]
        self._registrations.append(lambda bus: bus.extend(extension))

    @property
    def name(self) -> str:
        """The bounded context's name — its logger, its GlitchTip app, its OTel service."""
        return self._logger_name

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
        self.observability = observability
        self._sp_container.observability.override(observability)

    def _context_for_logs(self) -> Dict[str, Any]:
        """The execution's context, as fields on every log line this bus writes — its identity
        included, the execution's own over what the context was handed."""
        running = current_execution()
        context = {
            **self._get_context().copy(),
            **(running.chain() if running is not None else {}),
        }
        return {
            key: value
            for key, value in context.items()
            if isinstance(key, str) and key not in self._hidden_in_logs
        }

    def _add_dependencies_provided_by_user(self):
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

    def _assemble_handlers(self, bus: FrameworkBus) -> None:
        """Each handler wrapped by its interceptors, and who replaced whom — said once, in the
        build log."""
        self._wrap_handlers(bus)
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

    def build_root_bus(self):
        """Build the root bus with the dependencies provided by the user — nothing, for a
        reference to a context another service hosts (`is_reference`).

        Under a lock, and only once: two threads that reach a not-yet-built bus at the same
        time, as an async fan-out or an event fan-out does, would otherwise both build it and
        one of them would execute against a half-wired registry.
        """
        with self._build_lock:
            if self.is_reference or (self.was_initialized and self.bus is not None):
                return
            self._build_root_bus()

    def _build_root_bus(self):
        self._add_dependencies_provided_by_user()
        self._add_error_handlers_provided_by_user()
        for note in self._error_chain_notes():
            self.logger.warning(f"error handler: {note}")
        dto_registry = self._sp_container.dto_registry()

        self.bus = self._sp_container.framework_bus()  # type: ignore[assignment]
        self._assemble_handlers(self.bus)
        self._bind_context_to_handlers()

        # Set the loggers
        self.bus.log_after_execution = self.log_after_execution
        self.bus.feature_bus.log_after_execution = (
            self.log_after_execution and self.log_features
        )
        self.bus.app_service_bus.log_after_execution = (
            self.log_after_execution and self.log_app_services
        )

        # Set the DTO registry Tricky way but it works
        self.bus.dto_registry = dto_registry
        for handlers in (self.bus.feature_bus, self.bus.app_service_bus):
            handlers.context_owner = self
            handlers.context_providers = self._context_providers
        if self._execution_ids is not None:
            self.bus.feature_bus.new_execution_id = self._execution_ids
            self.bus.app_service_bus.new_execution_id = self._execution_ids

        # The container already injects this into the three buses; kept as a guard so
        # the guarantee does not depend on the provider still being the one the
        # framework_bus Factory captured.
        self.bus.observability = self.observability

        self.observability.start(self.logger)
        # Last, so a thread that reads the flag without the lock never sees a bus half wired.
        self.was_initialized = True

    def add_dependency(self, name, dep: Any):
        """
        Add a dependency to the framework where
        The Feature and App Service have as attribute

        Context: refused once the bus is built (`BusAlreadyBuilt`) — the
        Features were already wired, so a dependency added later would never
        reach them.
        """
        if self.was_initialized:
            raise BusAlreadyBuilt(
                f"dependency {name} registered late: '{self._logger_name}' is already built, so it would "
                "never be used — register it before the first execution"
            )
        if name in self.dynamic_dep_registry:
            error = DependencyAlreadyRegistered(f"The dependency {name} is already injected")
            self.observability.record_error(error, "", "framework", kind="framework")
            raise error
        self.dynamic_dep_registry[name] = dep
        self._registrations.append(lambda bus: bus.add_dependency(name, dep))

    @property
    def deps(self) -> TDeps:
        """Read-only locator of dependencies registered with ``add_dependency``.

        Inside a Feature / ApplicationService keep using ``self.<name>``.
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
        """
        registry = self.dto_registry
        dto_type = registry.get(name) or registry.get(f"{self.name}.{name}")
        if dto_type is None:
            raise UnknownDTOToExecute(f"no DTO or event registered under [{name}]")
        return rebuilt(read_values(payload), dto_type)

    def execution_ids(self, generator: Callable[[], str]) -> None:
        """Mint the `execution_id` of every execution of this bus with `generator` instead of a
        UUID v7 — a ULID, a UUID v4, a Snowflake id. Before the bus is built.

            billing.execution_ids(lambda: str(ulid.new()))

        Context: an id given from outside — `bus.context({"execution_id": …})`, a header — still
        wins for a root execution; the generator only mints the ones nobody gave.
        """
        if self.was_initialized:
            raise BusAlreadyBuilt(
                f"an execution id generator registered late: '{self._logger_name}' is already built, so it would "
                "never be used — register it before the first execution"
            )
        if not callable(generator):
            raise TypeError(
                f"execution_ids takes a function that returns a str, not {generator!r}"
            )
        self._execution_ids = generator
        self._registrations.append(lambda bus: bus.execution_ids(generator))

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
        """Main function to execute the framework — here, or on the service hosting this context
        when this bus is a reference to it (`is_reference`)."""
        if self.is_reference:
            return self._executed_where_hosted(dto, return_type)
        if not self.was_initialized:
            self.build_root_bus()

        if self.bus is None:
            error = SincproFrameworkNotBuilt(
                "Check the decorators are rigistering the features and app services, check the imports of each "
                "feature and app service"
            )
            self.observability.record_error(error, "", "framework", kind="framework")
            raise error

        if self._entered_here():
            return self.bus.execute(dto)
        with self._scope({}):
            return self.bus.execute(dto)

    @property
    def logger(self) -> LoggerProxy:
        """Get the bundle context logger."""
        if not self._is_logger_configured:
            self._logger = create_logger(self._logger_name)
            self._is_logger_configured = True
        return self._logger  # type: ignore[return-value]
