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
    overload,
)

from _typeshed import DataclassInstance, Incomplete
from sincpro_log.logger import LoggerProxy
from typing_extensions import Self

from sincpro_framework.observability import FrameworkSpanContext as FrameworkSpanContext
from sincpro_framework.observability import Observability as Observability

from . import ioc as ioc
from .aio import AsyncBus as AsyncBus
from .bus import FrameworkBus as FrameworkBus
from .context.domain.level import EntrypointKind
from .context.domain.store import ContextStore
from .context.entrypoint.bus import ContextMixin, FrameworkContext
from .context.entrypoint.facade import Context
from .context.infrastructure.providers import ContextProviderFunction
from .deps import TDeps
from .error_handler import ErrorHandler as ErrorHandler
from .exceptions import DependencyAlreadyRegistered as DependencyAlreadyRegistered
from .exceptions import SincproFrameworkNotBuilt as SincproFrameworkNotBuilt
from .interceptors import Interceptor
from .remote_execution.entrypoint.hosts import Attach, OpenHost
from .sincpro_abstractions import ApplicationService, DataTransferObject, Feature
from .sincpro_abstractions import TypeDTO as TypeDTO
from .sincpro_abstractions import TypeDTOResponse as TypeDTOResponse
from .sincpro_logger import create_logger as create_logger
from .transport.addresses import HostedAt

DTOClass = Type[DataTransferObject] | Type[DataclassInstance]
DTORegistration = DTOClass | list[DTOClass]

class UseFramework(ContextMixin, Generic[TDeps]):
    """
    Main class to use the framework, this is the main entry point to configure the framework.

    Parameterize with ``DependencyContextType`` so ``framework.deps`` is typed::

        framework = UseFramework[DependencyContextType]("my_context")
        framework.add_dependency("database", db_instance)
        framework.deps.database  # same instance Features receive as self.database
    """

    log_after_execution: bool
    log_app_services: bool
    log_features: bool

    dynamic_dep_registry: Dict[str, Any]
    observability: Observability
    global_error_handler: ErrorHandler | None
    feature_error_handler: ErrorHandler | None
    app_service_error_handler: ErrorHandler | None
    was_initialized: bool
    _logger_name: str
    # Override the parent bus type to allow None during initialization
    bus: FrameworkBus | None  # type: ignore[override]

    def __init__(
        self,
        bundled_context_name: str = "sincpro_framework",
        log_after_execution: bool = True,
        log_app_services: bool = True,
        log_features: bool = True,
        package: Optional[str] = None,
        hide_in_logs: Iterable[str] = (),
        metric_labels: Iterable[Any] = (),
    ) -> None:
        """
        Initialize the framework.

        Args:
            bundled_context_name: Name of the logger context / GlitchTip app
            log_after_execution: Enable/disable execution logging
            log_app_services: Enable/disable application service logging
            log_features: Enable/disable feature logging
            package: Optional Poetry distribution name used in Sentry release and OTel service.name
            hide_in_logs: Context keys kept off every signal (logs, spans, metrics, GlitchTip)
            metric_labels: Context keys on every metric series — "company" or
                of(BillingContext).company — beside release, service, version and tenant
        """
        ...
    # Improved overloads for framework execution
    @overload
    def __call__(self, dto: TypeDTO, return_type: Type[TypeDTOResponse]) -> TypeDTOResponse:
        """
        Execute a DTO with specified return type for better IDE support.

        Args:
            dto: The Data Transfer Object to execute
            return_type: The expected response DTO type

        Returns:
            The response DTO of the specified type
        """
        ...

    @overload
    def __call__(self, dto: TypeDTO) -> DataTransferObject | None:
        """
        Execute a DTO without specifying return type.

        Args:
            dto: The Data Transfer Object to execute

        Returns:
            The response DTO or None if no response
        """
        ...

    def get_async_bus(self) -> AsyncBus:
        """
        Return a stateless async facade over this framework's bus.

        Builds the root bus if it wasn't built yet (same lazy behavior as
        `__call__`). Use this from a caller that is itself `async def` and
        wants to fan out several DTOs concurrently, e.g. via `asyncio.gather`,
        without blocking its event loop.
        """
        ...

    def build_root_bus(self) -> None:
        """Build the root bus with the dependencies provided by the user."""
        ...

    def add_dependency(self, name: str, dep: Any) -> None:
        """
        Add a dependency to the framework.

        The dependency will be injected as an attribute into all Features and ApplicationServices.

        Args:
            name: The attribute name for the dependency
            dep: The dependency instance, function, or class

        Raises:
            DependencyAlreadyRegistered: If the dependency name is already registered
        """
        ...

    @property
    def deps(self) -> TDeps:
        """Read-only locator of dependencies registered with ``add_dependency``.

        Inside a Feature / ApplicationService keep using ``self.<name>``.
        Use this from the bounded-context root (SDK caller, test, entrypoint).
        """
        ...

    @property
    def dto_registry(self) -> Mapping[str, type]:
        """Every DTO name this bus answers, mapped to its class — read from the registrations,
        building nothing."""
        ...

    def map_to_dto_or_event(self, name: str, payload: str | dict[str, Any]) -> Any:
        """The DTO or event registered under `name`, rebuilt from raw data."""
        ...

    def feature[T: type](
        self, dto: DTORegistration, replaces: type | None = None
    ) -> Callable[[T], T]:
        """Register the decorated class as the Feature that answers `dto`; with `replaces`,
        instead of the Feature registered now, which is skipped from then on."""
        ...

    def app_service[T: type](
        self, dto: DTORegistration, replaces: type | None = None
    ) -> Callable[[T], T]:
        """Register the decorated class as the ApplicationService that answers `dto`; with
        `replaces`, instead of the one registered now."""
        ...

    def handler_of(self, dto: type) -> type | None:
        """The Feature or ApplicationService registered now for `dto` — what a `replaces=` names."""
        ...

    def replaced_for(self, dto: type) -> tuple[str, ...]:
        """`module.Class` of each handler of `dto` that `replaces=` took the place of."""
        ...

    def feature_handlers(self) -> dict[type, type]:
        """Every DTO a Feature answers now → the Feature, without building the bus."""
        ...

    def app_service_handlers(self) -> dict[type, type]:
        """Every DTO an ApplicationService answers now → the ApplicationService, without
        building the bus."""
        ...

    def handlers(self) -> dict[type, type]:
        """Every DTO answered now → the class answering it, without building the bus."""
        ...

    def extend(self, extension: Callable[[UseFramework[TDeps]], None]) -> None:
        """Wire a component into this bus and into every generation `fresh()` makes of it."""
        ...

    @property
    def hosted_at(self) -> HostedAt | None:
        """Where the context map — or `hosted_by` — hosts this bounded context; `None` when
        nothing places it elsewhere."""
        ...

    @property
    def is_reference(self) -> bool:
        """Whether this bus is a reference to a context another service hosts — never built
        here, every call forwarded."""
        ...

    @property
    def is_ready(self) -> bool:
        """Whether this bus can answer now — built, or a reference."""
        ...

    def run_here(self) -> None:
        """Run this context in this process, whatever the context map says — what serving it
        does."""
        ...

    def hosted_by(self, address: HostedAt | str) -> None:
        """Execute every DTO of this context on the service at `address` — a URL
        (`grpc://host:port?timeout=5`, `http://host:port`) or the typed `HostedAt`; refused with
        `InvalidAddress` when no wire reaches it.
        """
        ...

    @overload
    def serve(self, address: str) -> None: ...
    @overload
    def serve(self, address: str, attach: Literal[Attach.FOREGROUND]) -> None: ...
    @overload
    def serve(
        self,
        address: str,
        attach: Literal[Attach.THREAD, Attach.PROCESS],
    ) -> OpenHost:
        """Host this bounded context for other services at `address`, over gRPC — blocking, on
        this process's threads, or in a subprocess of its own."""
        ...

    @property
    def name(self) -> str:
        """The bounded context's name — its logger, its GlitchTip app, its OTel service."""
        ...

    def fresh(self) -> "UseFramework[TDeps]":
        """A new bus of this context, not built, with everything registered on this one again,
        in order — the same settings, dependencies and observability."""
        ...

    def interceptor[T: Interceptor](
        self,
        *commands: type,
        replaces: Interceptor | None = None,
        before: Sequence[Interceptor] = (),
        after: Sequence[Interceptor] = (),
        sequence: int = 10,
    ) -> Callable[[T], T]:
        """Run the decorated function around every execution of these Commands (all of this
        bus's when none is named) — outermost first, by before/after, sequence, registration;
        `replaces` runs it in the place of another. Refused after build.
        """
        ...

    def without_interceptor(self, interceptor: Interceptor) -> None:
        """Switch off an interceptor registered on this bus; refused after build."""
        ...

    def on_completion[L: Callable[..., Any]](self, listener: L) -> L:
        """Hear every use case of this bus that answered — an `ExecutionCompleted`."""
        ...

    def publish_completions(self, to: Any) -> None:
        """Hand every use case of this bus that answered to `to`, a Publisher."""
        ...

    def on_failure[L: Callable[..., Any]](self, listener: L) -> L:
        """Hear every failure of this bus that escaped its call — an `ExecutionFailed`."""
        ...

    def publish_failures(self, to: Any) -> None:
        """Hand every failure of this bus that escaped its call to `to`, a Publisher."""
        ...

    def execution_ids(self, generator: Callable[[], str]) -> None:
        """Mint every `execution_id` of this bus with `generator` instead of a UUID v7; an id
        given from outside still wins for a root execution. Refused after build."""
        ...

    def without_error_handler(self, handler: ErrorHandler) -> None:
        """Switch off an error handler of any of the three kinds registered on this bus."""
        ...

    def add_global_error_handler(
        self,
        handler: ErrorHandler,
        replaces: ErrorHandler | None = None,
        before: Sequence[ErrorHandler] = (),
        after: Sequence[ErrorHandler] = (),
        sequence: int = 10,
    ) -> None:
        """
        Add a global error handler. First registered = first to execute.

        On re-raise the framework delegates to the next handler in the chain.

        Args:
            handler: An ``ErrorHandler`` callable.
        """
        ...

    def add_feature_error_handler(
        self,
        handler: ErrorHandler,
        replaces: ErrorHandler | None = None,
        before: Sequence[ErrorHandler] = (),
        after: Sequence[ErrorHandler] = (),
        sequence: int = 10,
    ) -> None:
        """
        Add an error handler specifically for Feature errors.

        Same composition semantics as ``add_global_error_handler``.

        Args:
            handler: An ``ErrorHandler`` callable.
        """
        ...

    def add_app_service_error_handler(
        self,
        handler: ErrorHandler,
        replaces: ErrorHandler | None = None,
        before: Sequence[ErrorHandler] = (),
        after: Sequence[ErrorHandler] = (),
        sequence: int = 10,
    ) -> None:
        """
        Add an error handler specifically for ApplicationService errors.

        Same composition semantics as ``add_global_error_handler``.

        Args:
            handler: An ``ErrorHandler`` callable.
        """
        ...

    def ignore_sentry_exceptions(self, *exc_types: Type[Exception]) -> None:
        """Do not send these exception types to GlitchTip / Sentry."""
        ...

    def with_trace(
        self,
        trace_id: Optional[str] = None,
        span_id: Optional[str] = None,
        carrier: Optional[Mapping[str, Any]] = None,
    ) -> FrameworkSpanContext:
        """Create a tracing context manager for an execution block."""
        ...

    def with_parent_trace(self) -> FrameworkSpanContext:
        """Adopt the currently active OTel span for log correlation (no new span created)."""
        ...

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
        """A scope of this bus for the block — every execution inside reads it.

            with framework.context({"user_id": "123", "correlation_id": "abc"}) as app:
                app(some_dto)
            with framework.context({"tenant_id": "acme"}, global_scope=True): ...
            with framework.context(restore=["tenant:acme", f"session:{sid}"]): ...
            with framework.context(values, keep_as="sale-77", ttl=timedelta(hours=1)): ...

        The first scope of a flow is its entrypoint (`kind`, `DIRECT` by default); `restore` reads
        what a store kept, under `context_to_set`; `keep_as` keeps what the scope sees.
        """
        ...

    def current_context(self) -> Context:
        """The context in play as this bus sees it — the object `self.context` is, read-only."""
        ...

    def context_provider(
        self, needs: Sequence[str] = (), gives: Sequence[str] = ()
    ) -> Callable[[ContextProviderFunction], ContextProviderFunction]:
        """Register what gives `gives` from `needs`, run when an execution of this bus opens."""
        ...

    def context_schema(self, schema: type) -> None:
        """Validate and type what a scope of this bus is opened with against `schema`."""
        ...

    def context_store(self, store: ContextStore) -> None:
        """The store this bus's scopes keep and restore with."""
        ...

    @property
    def logger(self) -> LoggerProxy:
        """Get the framework logger instance."""
        ...
