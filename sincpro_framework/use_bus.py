import json
import threading
from datetime import timedelta
from typing import (
    Any,
    Callable,
    Dict,
    Generic,
    Iterable,
    Literal,
    Mapping,
    Optional,
    Sequence,
    Type,
    cast,
    get_type_hints,
    overload,
)

from pydantic import TypeAdapter
from sincpro_log.logger import LoggerProxy, create_logger

from . import ioc
from .aio import AsyncBus
from .bus import FrameworkBus
from .context.domain.level import EntrypointKind
from .context.domain.store import ContextStore
from .context.entrypoint.bus import ContextMixin, FrameworkContext
from .context.infrastructure.distributed import SharedContext, joined
from .context.infrastructure.providers import ContextProvider, ContextProviderFunction
from .context.infrastructure.tree import ROOT, current_execution, handed_on
from .deps import DependencyLocator, TDeps
from .error_handler import ErrorHandler, build_error_handler_chain
from .exceptions import (
    BusAlreadyBuilt,
    DependencyAlreadyRegistered,
    SincproFrameworkNotBuilt,
    UnknownDTOToExecute,
)
from .interceptors import Interceptor, InterceptorRegistration, chain_for
from .observability import FrameworkSpanContext, Observability
from .observability.correlation import declare_metric_labels
from .ordering import DEFAULT_SEQUENCE, Placement, name_of, ordered
from .remote_execution.adapters import transport_for
from .remote_execution.configuration import configured_host
from .remote_execution.entrypoint.hosts import Attach, OpenHost, serve_contexts
from .sincpro_abstractions import TypeDTO, TypeDTOResponse
from .transport.addresses import HostedAt


class _Forwarded:
    """What the async facade runs on its worker thread: the bus's own call — so every async call
    opens its scope as a sync one does (nothing one writes reaches the next), builds the bus on
    first use as a sync one does, and a context hosted by another service forwards.
    """

    def __init__(self, framework: "UseFramework") -> None:
        self._framework = framework

    def execute(self, dto: Any, return_type: Any = None) -> Any:
        return self._framework(dto, return_type)


class UseFramework(ContextMixin, Generic[TDeps]):
    """Main class to use the framework, this is the main entry point to configure the framework.

    Parameterize with the bounded context's ``DependencyContextType`` so registered
    adapters are typed on ``framework.deps``::

        framework = UseFramework[DependencyContextType]("my-context")
        framework.add_dependency("my_adapter", MyAdapter())
        framework.deps.my_adapter  # MyAdapter, same name Features get as self.my_adapter
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

        # Error handlers — ordered pipeline (first registered, first executed)
        self._global_error_handlers: list[Placement] = []
        self._feature_error_handlers: list[Placement] = []
        self._app_service_error_handlers: list[Placement] = []
        self._error_handlers_off: list[ErrorHandler] = []
        self.global_error_handler: Optional[ErrorHandler] = None
        self.feature_error_handler: Optional[ErrorHandler] = None
        self.app_service_error_handler: Optional[ErrorHandler] = None

        self._interceptors: list[InterceptorRegistration] = []
        self._interceptors_off: list[Interceptor] = []
        self._execution_ids: Callable[[], str] | None = None

        self.was_initialized: bool = False
        self._build_lock = threading.RLock()
        self.bus: FrameworkBus | None = None

        self._hosted_at: HostedAt | None = None
        """Where another service hosts this bounded context — the context map, or `hosted_by`."""
        self._runs_here: bool = False
        """Run in this process whatever the map says — it serves the context (`run_here`)."""
        configured = configured_host(bundled_context_name)
        if configured is not None:
            self._reach_at(configured)

    def _refuse_when_built(self, what: str) -> None:
        if self.was_initialized:
            raise BusAlreadyBuilt(
                f"{what} registered late: '{self._logger_name}' is already built, so it would "
                "never be used — register it before the first execution"
            )

    def feature(
        self, dto: ioc.DTORegistration, replaces: type | None = None
    ) -> Callable[[ioc.T], ioc.T]:
        """Register the decorated class as the Feature that answers `dto` (or each DTO listed).

            @billing.feature(CommandComputeTax, replaces=ComputeTax)

        Context: `replaces` names the Feature registered now; that one is skipped from then on,
        and the build log and introspection say who replaced it.
        """
        self._refuse_when_built(f"Feature for {ioc.names_of(dto)}")
        register = ioc.inject_feature_to_bus(self._sp_container, dto, replaces)

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
        self._refuse_when_built(f"ApplicationService for {ioc.names_of(dto)}")
        register = ioc.inject_app_service_to_bus(self._sp_container, dto, replaces)

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
        self._refuse_when_built(f"extension {getattr(extension, '__qualname__', extension)}")
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

    def _ordered_interceptors(self) -> list[InterceptorRegistration]:
        """The interceptors in the order they wrap, outermost first — see
        `sincpro_framework.ordering`. What was asked and could not be done as said — a
        replacement that wraps other Commands than the one it replaces, one that replaces or
        switches off what is not registered — is a warning, never a refusal."""
        by_interceptor = {one.interceptor: one for one in self._interceptors}
        result = ordered(
            [one.placement for one in self._interceptors], self._interceptors_off
        )
        for note in result.notes:
            self.logger.warning(f"interceptor: {note}")
        for replacement, chain in result.replaced.items():
            wraps, wrapped = (
                by_interceptor[replacement].commands,
                by_interceptor[chain[-1]].commands,
            )
            if set(wraps) != set(wrapped):
                self.logger.warning(
                    f"interceptor {name_of(replacement)} replaces {name_of(chain[-1])} but wraps "
                    f"{', '.join(one.__name__ for one in wraps) or 'every Command'}, where that "
                    f"one wrapped {', '.join(one.__name__ for one in wrapped) or 'every Command'}"
                )
            self.logger.info(
                f"interceptor {name_of(chain[-1])} is replaced by {name_of(replacement)}"
            )
        for switched_off in result.off:
            self.logger.info(f"interceptor {name_of(switched_off)} is switched off")
        return [by_interceptor[one] for one in result.items]

    def _assemble_handlers(self, bus: FrameworkBus) -> None:
        feature_bus, app_service_bus = bus.feature_bus, bus.app_service_bus
        answered = {*feature_bus.feature_registry, *app_service_bus.app_service_registry}
        interceptors = self._ordered_interceptors()
        for registration in interceptors:
            for command in registration.commands:
                if command not in answered:
                    raise UnknownDTOToExecute(
                        f"interceptor {registration.name} wraps {command.__name__}, which no "
                        f"Feature or ApplicationService on '{self._logger_name}' answers"
                    )
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
        feature_bus.interceptors = {
            command: chain_for(command, interceptors)
            for command in feature_bus.feature_registry
        }
        app_service_bus.interceptors = {
            command: chain_for(command, interceptors)
            for command in app_service_bus.app_service_registry
        }

    def _add_error_handlers_provided_by_user(self):
        if self.global_error_handler:
            self._sp_container.framework_bus.add_attributes(
                handle_error=self.global_error_handler
            )

        if self.feature_error_handler:
            self._sp_container.feature_bus.add_attributes(
                handle_error=self.feature_error_handler
            )

        if self.app_service_error_handler:
            self._sp_container.app_service_bus.add_attributes(
                handle_error=self.app_service_error_handler
            )

    def _error_chain_notes(self) -> list[str]:
        """What the three chains were asked and could not do as said — told when the bus is
        built, once, rather than at every handler added."""
        chains = (
            self._global_error_handlers,
            self._feature_error_handlers,
            self._app_service_error_handlers,
        )
        registered = {one.item for chain in chains for one in chain}
        notes = [
            note
            for chain in chains
            for note in ordered(
                chain, [one for one in self._error_handlers_off if one in registered]
            ).notes
        ]
        return notes + [
            f"{name_of(handler)} is switched off, but it is not registered here"
            for handler in self._error_handlers_off
            if handler not in registered
        ]

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
        self._refuse_when_built(f"dependency {name}")
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
        """Every DTO name this bus answers, mapped to its class — read from the registrations,
        so it builds nothing: a reference answers it as the service hosting it does.

        A live query, not a snapshot: it always answers what is registered at the moment it's
        asked, not what was registered when this property was first read.
        """
        return self._sp_container.dto_registry.kwargs

    def map_to_dto_or_event(self, name: str, payload: "str | dict[str, Any]") -> Any:
        """The DTO or event registered under `name`, rebuilt from raw data.

        What a queue consumer has once a message arrives off the wire — Kafka, RabbitMQ,
        this framework's own `BackgroundQueue` — ready to hand to ``bus(...)``.
        """
        dto_type = self.dto_registry.get(name)
        if dto_type is None:
            raise UnknownDTOToExecute(f"no DTO or event registered under [{name}]")
        if hasattr(dto_type, "from_json"):
            return dto_type.from_json(payload)
        raw = json.loads(payload) if isinstance(payload, str) else payload
        return dto_type.model_validate(raw)

    def interceptor[T: Interceptor](
        self,
        *commands: type,
        replaces: Interceptor | None = None,
        before: Sequence[Interceptor] = (),
        after: Sequence[Interceptor] = (),
        sequence: int = DEFAULT_SEQUENCE,
    ) -> Callable[[T], T]:
        """Run the decorated function around every execution of these Commands — all of this
        bus's when none is named — however they are reached.

            @billing.interceptor(CommandCreateInvoice)
            def credit_check(dto, call_next): ...

            @billing.interceptor(CommandCreateInvoice, replaces=credit_check)   in its place
            @billing.interceptor(CommandCreateInvoice, before=[credit_check])    outside it

        Context: the first to run is the outermost. They run by `before`/`after`, then
        `sequence` (lower first), then the order they were registered — the order every
        extension point uses, see `sincpro_framework.ordering`; a replacement takes the place
        of what it replaces and wraps the same Commands. Fixed when the bus is built; one
        registered later would never run, so it is refused. See `sincpro_framework.interceptors`
        for what an interceptor may and may not do.
        """

        def register(interceptor: T) -> T:
            self._refuse_when_built(f"interceptor {interceptor.__qualname__}")
            self._interceptors.append(
                InterceptorRegistration(
                    interceptor, commands, sequence, tuple(before), tuple(after), replaces
                )
            )
            self._registrations.append(
                lambda bus: bus.interceptor(
                    *commands,
                    replaces=replaces,
                    before=before,
                    after=after,
                    sequence=sequence,
                )(interceptor)
            )
            return interceptor

        return register

    def without_interceptor(self, interceptor: Interceptor) -> None:
        """Switch off an interceptor registered on this bus — it wraps nothing from then on.
        Refused once the bus is built, like registering one."""
        self._refuse_when_built(f"switching off interceptor {interceptor.__qualname__}")
        self._interceptors_off.append(interceptor)
        self._registrations.append(lambda bus: bus.without_interceptor(interceptor))

    def on_completion[L: Callable[..., Any]](self, listener: L) -> L:
        """Hear every use case of this bus that answered — an `ExecutionCompleted` with the DTO and
        the response, in this process. A decorator.

            @billing.on_completion
            def done(completed: ExecutionCompleted) -> None: ...

        Context: one per Feature and ApplicationService that returned, the Features an
        ApplicationService runs included; none when an error handler answered instead. A listener
        that raises is logged and reaches nothing else. Every bus of the process:
        `sincpro_framework.outcomes.completions.subscribe(listener)`.
        """
        from .outcomes import completions

        completions.subscribe(listener, self.name)
        return listener

    def publish_completions(self, to: Any) -> None:
        """Hand every use case of this bus that answered to `to` — a `Publisher` over any queue:
        another bus, a broker topic, the outbox. A delivery that fails is logged.

            billing.publish_completions(to=Publisher(FastStreamQueue(kafka)))
        """
        from .outcomes import completions, publishing

        completions.subscribe(publishing(to), self.name)

    def on_failure[L: Callable[..., Any]](self, listener: L) -> L:
        """Hear every failure of this bus that escaped its call — an `ExecutionFailed`, in this
        process. A decorator.

            @billing.on_failure
            def alert(failure: ExecutionFailed) -> None: ...

        Context: emitted once, where the caller receives the exception, never when an error
        handler answered instead. A listener that raises is logged and reaches nothing else. Every
        bus of the process: `sincpro_framework.outcomes.failures.subscribe(listener)`.
        """
        from .outcomes import failures

        failures.subscribe(listener, self.name)
        return listener

    def publish_failures(self, to: Any) -> None:
        """Hand every failure of this bus that escaped its call to `to` — a `Publisher` over any
        queue: another bus, a broker topic (the error queue), the outbox. Fire and forget: a
        delivery that fails is logged.

            billing.publish_failures(to=Publisher(FastStreamQueue(kafka)))
        """
        from .outcomes import failures, publishing

        failures.subscribe(publishing(to), self.name)

    def execution_ids(self, generator: Callable[[], str]) -> None:
        """Mint the `execution_id` of every execution of this bus with `generator` instead of a
        UUID v7 — a ULID, a UUID v4, a Snowflake id. Before the bus is built.

            billing.execution_ids(lambda: str(ulid.new()))

        Context: an id given from outside — `bus.context({"execution_id": …})`, a header — still
        wins for a root execution; the generator only mints the ones nobody gave.
        """
        self._refuse_when_built("an execution id generator")
        if not callable(generator):
            raise TypeError(
                f"execution_ids takes a function that returns a str, not {generator!r}"
            )
        self._execution_ids = generator
        self._registrations.append(lambda bus: bus.execution_ids(generator))

    def _error_chain(self, placements: list[Placement]) -> Optional[ErrorHandler]:
        registered = {one.item for one in placements}
        off = [one for one in self._error_handlers_off if one in registered]
        return build_error_handler_chain(ordered(placements, off).items)

    def _apply_error_chains(self) -> None:
        """Each chain in the order every extension point uses — first to run is the first to
        see the error — and handed to the bus at once when it is already built."""
        self.global_error_handler = self._error_chain(self._global_error_handlers)
        self.feature_error_handler = self._error_chain(self._feature_error_handlers)
        self.app_service_error_handler = self._error_chain(self._app_service_error_handlers)
        if self.was_initialized and self.bus is not None:
            self.bus.handle_error = self.global_error_handler
            self.bus.feature_bus.handle_error = self.feature_error_handler
            self.bus.app_service_bus.handle_error = self.app_service_error_handler

    def _add_error_handler(
        self,
        placements: list[Placement],
        handler: ErrorHandler,
        replaces: ErrorHandler | None,
        before: Sequence[ErrorHandler],
        after: Sequence[ErrorHandler],
        sequence: int,
    ) -> None:
        if not callable(handler):
            raise TypeError("The handler must be a callable")
        placements.append(Placement(handler, sequence, tuple(before), tuple(after), replaces))
        self._apply_error_chains()

    def without_error_handler(self, handler: ErrorHandler) -> None:
        """Switch off an error handler of any of the three kinds — one registered later too;
        one never registered is a warning when the bus is built."""
        self._error_handlers_off.append(handler)
        self._registrations.append(lambda bus: bus.without_error_handler(handler))
        self._apply_error_chains()

    def add_global_error_handler(
        self,
        handler: ErrorHandler,
        replaces: ErrorHandler | None = None,
        before: Sequence[ErrorHandler] = (),
        after: Sequence[ErrorHandler] = (),
        sequence: int = DEFAULT_SEQUENCE,
    ) -> None:
        """Register a global error handler.

        Signature: ``(error) -> Any``. First registered = first to execute.

        **What the handler returns becomes the bus's answer, and the error is gone.**
        Re-raise to pass it on: to the next handler in the chain, or — from the last one — to
        whoever called the bus. This is the part that catches people out, because a handler
        written to *watch* errors is the common case and returns ``None`` without meaning to::

            app.add_global_error_handler(lambda error: log.error(error))
            app(SomeCommand())        # returns None. The failure is gone and nobody knows.

        A handler that only wants to look at the error ends with ``raise``::

            def logger(error):
                log.error(error)
                raise error           # seen, and still an error

        With no handler registered at all, the exception simply propagates.

        Example of the two kinds together::

            def base(error):
                return ErrorResponse(str(error))    # answers, and stops here

            app.add_global_error_handler(base)

            def logger(error):
                log.error(error)
                raise error                          # delegates to base

            app.add_global_error_handler(logger)
        """
        self._add_error_handler(
            self._global_error_handlers, handler, replaces, before, after, sequence
        )
        self._registrations.append(
            lambda bus: bus.add_global_error_handler(
                handler, replaces, before, after, sequence
            )
        )

    def add_feature_error_handler(
        self,
        handler: ErrorHandler,
        replaces: ErrorHandler | None = None,
        before: Sequence[ErrorHandler] = (),
        after: Sequence[ErrorHandler] = (),
        sequence: int = DEFAULT_SEQUENCE,
    ) -> None:
        """Register a feature-level error handler, for errors raised inside a `Feature`.

        Same semantics as `add_global_error_handler`, including the one that surprises people:
        what the handler returns becomes the bus's answer, and only a re-raise passes the error
        on.
        """
        self._add_error_handler(
            self._feature_error_handlers, handler, replaces, before, after, sequence
        )
        self._registrations.append(
            lambda bus: bus.add_feature_error_handler(
                handler, replaces, before, after, sequence
            )
        )

    def add_app_service_error_handler(
        self,
        handler: ErrorHandler,
        replaces: ErrorHandler | None = None,
        before: Sequence[ErrorHandler] = (),
        after: Sequence[ErrorHandler] = (),
        sequence: int = DEFAULT_SEQUENCE,
    ) -> None:
        """Register an error handler for errors raised inside an `ApplicationService`.

        Same semantics as `add_global_error_handler`, including the one that surprises people:
        what the handler returns becomes the bus's answer, and only a re-raise passes the error
        on.
        """
        self._add_error_handler(
            self._app_service_error_handlers, handler, replaces, before, after, sequence
        )
        self._registrations.append(
            lambda bus: bus.add_app_service_error_handler(
                handler, replaces, before, after, sequence
            )
        )

    def ignore_sentry_exceptions(self, *exc_types: Type[Exception]) -> None:
        """Do not send these exception types to GlitchTip / Sentry.

        Error handlers still run. Use this for expected domain errors
        (validation, insufficient funds, etc.) so they do not hide real bugs:
        anything not in this list is reported even if a handler swallows it.
        """
        self.observability.ignore(*exc_types)

    def context(
        self,
        context_to_set: Mapping[Any, Any] | None = None,
        global_scope: bool = False,
        kind: EntrypointKind | None = None,
        restore: str | list[str] | None = None,
        keep_as: str | None = None,
        ttl: timedelta | None = None,
        store: ContextStore | None = None,
    ) -> FrameworkContext:
        """A scope of this bus for the block — every execution inside reads it, nothing outside does.

            with app.context({"correlation_id": "123", "user_id": "456"}) as app_with_context:
                app_with_context(some_dto)

            with app.context({"tenant_id": "acme"}, global_scope=True):    every execution of
                ...                                                          this bus reads it
            with app.context(restore=["tenant:acme", f"session:{sid}"]):    what a store kept
            with app.context(values, keep_as="sale-77", ttl=timedelta(hours=1)):

        Context: isolated per thread and task. The first scope of a flow is its entrypoint —
        `kind` says which entrance opened it, `EntrypointKind.DIRECT` when none is named; a scope
        inside another is a child of it. `restore` reads each key from the store (`store`, else
        the nearest `ContextStore` in the context), in order, under `context_to_set`; `keep_as`
        keeps what the scope sees, with the flow's chain, for whoever resumes it.
        """
        return FrameworkContext(
            cast(Any, self), context_to_set, global_scope, kind, restore, keep_as, ttl, store
        )

    def context_provider(
        self, needs: Sequence[str] = (), gives: Sequence[str] = ()
    ) -> Callable[[ContextProviderFunction], ContextProviderFunction]:
        """Register the decorated function as what gives `gives` from `needs` — run when an
        execution of this bus opens, has every key it needs and lacks one it gives.

            @siat_soap_sdk.context_provider(needs=["nit_id"], gives=["TOKEN", "SIAT_ENV"])
            def siat_credentials(context): ...

        Its answer is written on that execution's node: what it runs finds it there.
        """

        def register(function: ContextProviderFunction) -> ContextProviderFunction:
            self._context_providers.append(
                ContextProvider(function, tuple(needs), tuple(gives))
            )
            self._registrations.append(
                lambda bus: bus.context_provider(needs, gives)(function)
            )
            return function

        return register

    def context_schema(self, schema: type) -> None:
        """Validate and type what a scope of this bus is opened with against `schema` — the
        `TypedDict` or DTO a project declares its context with:

            siat_soap_sdk.context_schema(SIATContext)     SIAT_ENV=2 arrives a SIATEnvironment

        The keys the schema names are validated; any other key passes as it was given."""
        self._context_schema = TypeAdapter(schema)
        self._registrations.append(lambda bus: bus.context_schema(schema))

    def context_store(
        self,
        store: ContextStore,
        ttl: timedelta = timedelta(hours=24),
        every: timedelta = timedelta(seconds=1),
    ) -> None:
        """Share this bus's context through `store` — and keep and restore with it.

            billing.context_store(KeyValueContexts(RedisKeyValue(redis)))

        The API does not change: `self.context`, `use_context()`, `bus.context(...)` read and
        write as always, and every node of this bus's executions is kept in the store for `ttl`.
        A bus of another service — another project too — that set the same store reads the chain
        it was handed on, the process is kept per service, and `Level.GLOBAL` is shared by every
        one of them; what others wrote is read again at most once per `every`. Nothing is shared
        by a bus that did not say so. See `docs/prd/PRD_24_shared-context.md`.
        """
        self._published.set(ContextStore, store)
        self._shared_context = SharedContext(store, ttl, every)
        joined(ROOT, self._shared_context, self.observability.identity.service_name)
        self._registrations.append(lambda bus: bus.context_store(store, ttl=ttl, every=every))

    def with_trace(
        self,
        trace_id: Optional[str] = None,
        span_id: Optional[str] = None,
        carrier: Optional[Mapping[str, Any]] = None,
    ) -> FrameworkSpanContext:
        """Create a tracing context manager for an execution block.

        Binds trace_id/span_id to all internal logs and makes them available in
        Feature/ApplicationService via self.context.get("trace_id").

        When opentelemetry is installed, also attaches an OTel span context so
        all bus spans are exported under the correct parent trace.

        Args:
            trace_id: Explicit trace identifier (hex string for OTel compatibility,
                      or any string for log-only correlation). Auto-generated if None.
            span_id:  Explicit span identifier. Auto-generated if None.
            carrier:  Mapping with W3C traceparent/tracestate headers (e.g.
                      request.headers). Extracts the parent trace from the headers.
                      Requires opentelemetry to be installed.

        Returns:
            FrameworkSpanContext ready to use with the ``with`` statement.

        Examples::

            # Fresh trace — auto-generated IDs
            with app.with_trace() as traced:
                result = traced(MyDTO(...))

            # Propagate from explicit IDs
            with app.with_trace(trace_id="4bf92f35...", span_id="00f067aa...") as traced:
                result = traced(MyDTO(...))

            # Extract from incoming W3C headers (OTel)
            with app.with_trace(carrier=request.headers) as traced:
                result = traced(MyDTO(...))

            # Compose with context()
            with app.with_trace(carrier=headers) as traced:
                with traced.context({"user_id": "u-123"}) as app_with_ctx:
                    result = app_with_ctx(MyDTO(...))
        """
        self.build_root_bus()
        return self.observability.trace_context(
            cast(Any, self), trace_id=trace_id, span_id=span_id, carrier=carrier
        )

    def with_parent_trace(self) -> FrameworkSpanContext:
        """Adopt the currently active OTel span for log correlation and context injection.

        Unlike ``with_trace()``, this does **not** create a new root span. It reads
        the ``trace_id`` and ``span_id`` from whatever span is currently active in the
        process — typically a span created by the host application's instrumentation
        (Odoo WSGI middleware, FastAPI OpenTelemetry middleware, a Celery task decorator,
        etc.) — and uses them to correlate logs and make them available inside
        Feature / ApplicationService via ``self.context.get("trace_id")``.

        Because no new span is created and the OTel context is not modified, DTO spans
        produced by the bus are **direct children** of the active span, not grandchildren
        through an intermediate root span as ``with_trace()`` would produce::

            # with with_trace()
            host-span
              └── my-service (root span added by the framework)
                    └── CreateOrderDTO (application_service)

            # with with_parent_trace()
            host-span
              └── CreateOrderDTO (application_service)   ← direct child, no extra level

        Falls back to UUID-based log correlation when no valid OTel span is active or
        when opentelemetry is not installed, so it is always safe to call.

        Examples::

            # Inside an Odoo controller or FastAPI handler where the
            # host has already started a span
            with framework.with_parent_trace() as fw:
                result = fw(CreateOrderDTO(...), OrderResult)
        """
        self.build_root_bus()
        return self.observability.trace_context(cast(Any, self), adopt_active=True)

    def get_async_bus(self) -> AsyncBus:
        """Return a stateless async facade over this framework's bus.

        Each call runs the bus's own call on a worker thread, so the bus is built on first use
        (same lazy behavior as ``__call__``) and a reference forwards. Use this from a caller
        that is itself ``async def`` and wants to fan out several DTOs concurrently, e.g. via
        ``asyncio.gather``, without blocking its event loop. See ``Bus.get_async_bus`` /
        ``AsyncBus`` for the propagation and reuse semantics.
        """
        return AsyncBus(_Forwarded(self))

    def _declared_response(self, dto_type: type) -> Any:
        """What the handler of `dto_type` declares it answers — `None` when it declares nothing
        a value can be rebuilt as (`Any`), so the answer comes back as the host sent it."""
        handler = self.handler_of(dto_type)
        declared = None if handler is None else get_type_hints(handler.execute).get("return")
        return None if declared is Any else declared

    def _executed_where_hosted(self, dto: Any, return_type: Any) -> Any:
        """The DTO executed by the service hosting this context, answered as `return_type` —
        or the response its handler here declares, when none is given.

        Context: it is handed the context of the execution calling it — its caller's, then what
        this bus was given — caused by that execution and in its flow (`handed_on`)."""
        hosted_at = self._hosted_at
        if hosted_at is None:
            raise SincproFrameworkNotBuilt(f"'{self.name}' is not hosted by another service")
        response = (
            return_type if return_type is not None else self._declared_response(type(dto))
        )
        context = handed_on({**self._inherited_context(), **self.current_context()})
        return transport_for(hosted_at).execute(self.name, dto, response, context)

    @property
    def hosted_at(self) -> HostedAt | None:
        """Where the context map — or `hosted_by` — hosts this bounded context; `None` when
        nothing places it elsewhere."""
        return self._hosted_at

    @property
    def is_reference(self) -> bool:
        """Whether this bus is a reference to a context another service hosts: a client with the
        face of the bus — never built here, no Feature instantiated, no dependency resolved —
        that forwards every call. A context this process runs (`run_here`) never is one."""
        return self._hosted_at is not None and not self._runs_here

    @property
    def is_ready(self) -> bool:
        """Whether this bus can answer now — built, or a reference, which has nothing to build."""
        return self.is_reference or (self.was_initialized and self.bus is not None)

    def run_here(self) -> None:
        """Run this context in this process, whatever the context map says — what serving it
        does (`serve`, an open host), so a service whose map names itself never calls itself.
        """
        if not self._runs_here:
            self._runs_here = True
            self._registrations.append(lambda bus: bus.run_here())

    def _reach_at(self, hosted_at: HostedAt) -> None:
        """Point this bus at the service hosting it — said once in the logs, so a process's start
        shows every context it reaches elsewhere."""
        self._hosted_at = hosted_at
        self.logger.info(
            f"{self.name} is hosted at {hosted_at}: this bus forwards every call"
        )

    def hosted_by(self, address: HostedAt | str) -> None:
        """Execute every DTO of this context on the service at `address` — what the context map
        says, in code: a URL, or the typed address.

            billing.hosted_by("grpc://10.0.0.5:50051?timeout=5")
            billing.hosted_by(HostedAt(Wire.HTTPS, "billing-service:443", timeout=5))

        An address no wire reaches is refused here (`InvalidAddress`), saying what to write.

        See `docs/entrypoints/bounded-contexts-across-services.md`.
        """
        self._reach_at(HostedAt.of(address))
        self._registrations.append(lambda bus: bus.hosted_by(address))

    @overload
    def serve(self, address: str) -> None: ...

    @overload
    def serve(self, address: str, attach: Literal[Attach.FOREGROUND]) -> None: ...

    @overload
    def serve(
        self,
        address: str,
        attach: Literal[Attach.THREAD, Attach.PROCESS],
    ) -> OpenHost: ...

    def serve(self, address: str, attach: Attach = Attach.FOREGROUND) -> OpenHost | None:
        """Host this bounded context for other services at `address` — `host:port`, `:0` for a
        free port — over gRPC.

            billing.serve("0.0.0.0:50051")                           its own deployment: blocks
            host = billing.serve("0.0.0.0:50051", Attach.THREAD)     beside a REST API
            host = billing.serve("0.0.0.0:50051", Attach.PROCESS)    a subprocess of its own
            host.stop()

        Several at once: `serve_contexts([billing, catalog], address, attach)`.
        """
        hosted = [cast(Any, self)]
        if attach == Attach.FOREGROUND:
            serve_contexts(hosted, address, Attach.FOREGROUND)
            return None
        if attach == Attach.THREAD:
            return serve_contexts(hosted, address, Attach.THREAD)
        return serve_contexts(hosted, address, Attach.PROCESS)

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
