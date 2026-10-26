"""The typed surface of `UseFramework` — what an IDE shows a project: every mixin's methods on
one class, `bus(dto, ResponseType)` answering that type, `bus.deps` typed by its
`DependencyContextType`. The implementation is `use_bus.py`.
"""

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

from _typeshed import DataclassInstance
from sincpro_log.logger import LoggerProxy

from sincpro_framework.observability import Observability
from sincpro_framework.observability.tracing.span_context import FrameworkSpanContext

from .aio import AsyncBus
from .bus import FrameworkBus
from .bus_pipeline.interceptors.interceptor import Interceptor
from .circuit_breaker import CircuitBreaker
from .context.domain.level import EntrypointKind
from .context.domain.providers import ContextProviderFunction
from .context.domain.store import ContextStore
from .context.entrypoint.bus import FrameworkContext
from .context.entrypoint.bus_context import BusContextMixin
from .context.entrypoint.facade import Context
from .data_layer.caching.entrypoint.facades import CacheFacade, IdempotencyFacade
from .dependencies import TDeps
from .error_handler import ErrorHandler
from .remote_execution.domain.address import HostedAt
from .remote_execution.entrypoint.hosts import Attach, OpenHost
from .sincpro_abstractions import DataTransferObject, TypeDTO, TypeDTOResponse

DTOClass = Type[DataTransferObject] | Type[DataclassInstance]
DTORegistration = DTOClass | list[DTOClass]

class UseFramework(BusContextMixin, Generic[TDeps]):
    """
    One bounded context's bus — where its use cases are registered, its dependencies
    wired, and it is called.

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
    _name: str
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
        """A bus for the bounded context `bundled_context_name`, built on its first call.

            UseFramework("billing", hide_in_logs=["TOKEN"], metric_labels=["company"])

        The name is its logger, its GlitchTip app and the prefix of its use cases' names.
        `log_*` silence execution logs (all, or one kind); `package` is the distribution named
        in the Sentry release and OTel `service.name`; `hide_in_logs` removes context keys from
        every signal; `metric_labels` puts context keys on every metric series — a key
        (`"company"`) or `field_path(BillingContext).company` — declared before the build.
        """
        ...
    # Improved overloads for framework execution
    @overload
    def __call__(self, dto: TypeDTO, return_type: Type[TypeDTOResponse]) -> TypeDTOResponse:
        """Execute `dto` and answer its response typed as `return_type` — for the IDE; the
        type is not checked at runtime.

            billing(CommandIssueInvoice(total=100), ResponseInvoice)  ->  ResponseInvoice
        """
        ...

    @overload
    def __call__(self, dto: TypeDTO) -> DataTransferObject | None:
        """Execute `dto` and answer what its use case answered — `None` when it answers
        nothing."""
        ...

    def get_async_bus(self) -> AsyncBus:
        """A stateless async facade over this bus — each call runs on a worker thread, so an
        `async def` caller fans out DTOs with `asyncio.gather` without blocking its loop. The
        bus is built on first use, as `__call__` builds it."""
        ...

    def build_root_bus(self) -> None:
        """Bring the bus up to date now: build it, or build a new generation when something was
        registered after the last one. The first execution does it by itself; called on purpose,
        a wiring error surfaces at that line."""
        ...

    @property
    def revision(self) -> int:
        """How many registrations reached this bus after it was built — what a snapshot of the
        bus compares to know it is still true."""
        ...

    def add_dependency(self, name: str, dep: Any, replaces: Any = None) -> None:
        """Register `dep` under `name` — every Feature and ApplicationService reads it as
        `self.<name>`. A name already taken is refused (`DependencyAlreadyRegistered`) unless
        `replaces` names what is there; naming something else only warns. Added after the
        build, it reaches the handlers from the next execution.

            billing.add_dependency("cache", RedisCache(...), replaces=memory_cache)
        """
        ...

    @property
    def deps(self) -> TDeps:
        """Read-only locator of dependencies registered with ``add_dependency``.

        Inside a Feature / ApplicationService still use ``self.<name>``.
        Use this from the bounded-context root (SDK caller, test, entrypoint).
        """
        ...

    @property
    def dto_registry(self) -> Mapping[str, type]:
        """Every DTO name this bus answers, mapped to its class — read from the registrations,
        building nothing."""
        ...

    def map_to_dto_or_event(self, name: str, payload: str | bytes | Mapping[str, Any]) -> Any:
        """The DTO or event registered under `name` — its identity, or its class name within
        this bus — rebuilt from its JSON values."""
        ...

    def feature[T: type](
        self, dto: DTORegistration, replaces: type | None = None
    ) -> Callable[[T], T]:
        """Register the decorated class as the Feature that answers `dto`; with `replaces`,
        instead of the Feature registered now, which is skipped from then on. Registered after
        the bus was built, it answers from the next execution."""
        ...

    def app_service[T: type](
        self, dto: DTORegistration, replaces: type | None = None
    ) -> Callable[[T], T]:
        """Register the decorated class as the ApplicationService that answers `dto`; with
        `replaces`, instead of the one registered now. Registered after the bus was built, it
        answers from the next execution."""
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
        """Wire a component into this bus and into every bus `fresh()` makes of it —
        `extension(bus)` registers its interceptors, dependencies or handlers. After the bus
        was built, the extension answers from the next execution, whole."""
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

    def hosted_by(
        self, address: HostedAt | str, circuit_breaker: CircuitBreaker | bool | None = None
    ) -> None:
        """Execute every DTO of this context on the service at `address` — a URL
        (`grpc://host:port?timeout=5`, `http://host:port?circuit_breaker=on`) or the typed `HostedAt`;
        refused with `InvalidAddress` when no wire reaches it. `circuit` stops calling it while it
        does not answer — `True` the address's own circuit, a `CircuitBreaker` that one, `False` none;
        left out, the address decides (none by default).
        """
        ...

    @overload
    def serve(self, address: str) -> None:
        """Host this bounded context at `address`, over gRPC — blocking until it stops."""
        ...

    @overload
    def serve(self, address: str, attach: Literal[Attach.FOREGROUND]) -> None:
        """The same, said explicitly: blocking until it stops."""
        ...

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
        `replaces` runs it in the place of another. Registered after the build, it wraps from
        the next execution.
        """
        ...

    def without_interceptor(self, interceptor: Interceptor) -> None:
        """Switch off an interceptor registered on this bus; after the build, from the next
        execution."""
        ...

    @overload
    def on_start[L: Callable[..., Any]](
        self, listener: L, use_cases: Iterable[type | str] | None = None, when: Any = None
    ) -> L:
        """Hear every use case of this bus that begins — an `ExecutionStarted`. `use_cases` /
        `when` hear less."""
        ...

    @overload
    def on_start[L: Callable[..., Any]](
        self,
        listener: None = None,
        use_cases: Iterable[type | str] | None = None,
        when: Any = None,
    ) -> Callable[[L], L]:
        """The same, as a decorator with `use_cases` / `when`."""
        ...

    def publish_starts(
        self, to: Any, use_cases: Iterable[type | str] | None = None, when: Any = None
    ) -> None:
        """Hand every use case of this bus that begins to `to`, a Publisher. `use_cases` /
        `when` hand on less."""
        ...

    @overload
    def on_completion[L: Callable[..., Any]](
        self, listener: L, use_cases: Iterable[type | str] | None = None, when: Any = None
    ) -> L:
        """Hear every use case of this bus that answered — an `ExecutionCompleted`.
        `use_cases` / `when` hear less."""
        ...

    @overload
    def on_completion[L: Callable[..., Any]](
        self,
        listener: None = None,
        use_cases: Iterable[type | str] | None = None,
        when: Any = None,
    ) -> Callable[[L], L]:
        """The same, as a decorator with `use_cases` / `when`."""
        ...

    def publish_completions(
        self, to: Any, use_cases: Iterable[type | str] | None = None, when: Any = None
    ) -> None:
        """Hand every use case of this bus that answered to `to`, a Publisher. `use_cases` /
        `when` hand on less."""
        ...

    @overload
    def on_failure[L: Callable[..., Any]](
        self, listener: L, use_cases: Iterable[type | str] | None = None, when: Any = None
    ) -> L:
        """Hear every failure of this bus that escaped its call — an `ExecutionFailed`.
        `use_cases` / `when` hear less."""
        ...

    @overload
    def on_failure[L: Callable[..., Any]](
        self,
        listener: None = None,
        use_cases: Iterable[type | str] | None = None,
        when: Any = None,
    ) -> Callable[[L], L]:
        """The same, as a decorator with `use_cases` / `when`."""
        ...

    def publish_failures(
        self, to: Any, use_cases: Iterable[type | str] | None = None, when: Any = None
    ) -> None:
        """Hand every failure of this bus that escaped its call to `to`, a Publisher.
        `use_cases` / `when` hand on less."""
        ...

    def execution_ids(self, generator: Callable[[], str]) -> None:
        """Mint every `execution_id` of this bus with `generator` instead of a UUID v7; an id
        given from outside still wins for a root execution. Given after the build, it mints from
        the next execution."""
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
        """Answer any failure of this bus with `handler` — `(error) -> answer`, first
        registered first; `before` / `after` / `sequence` place it, `replaces` puts it where
        another was.

        What it returns is the bus's answer and the error is gone; a handler that only watches
        ends with `raise`, handing the error to the next one — from the last, to the caller.

            billing.add_global_error_handler(not_found)
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
        """Answer a Feature's failure with `handler` — the same chain rules as
        `add_global_error_handler`."""
        ...

    def add_app_service_error_handler(
        self,
        handler: ErrorHandler,
        replaces: ErrorHandler | None = None,
        before: Sequence[ErrorHandler] = (),
        after: Sequence[ErrorHandler] = (),
        sequence: int = 10,
    ) -> None:
        """Answer an ApplicationService's failure with `handler` — the same chain rules as
        `add_global_error_handler`."""
        ...

    def ignore_sentry_exceptions(self, *exc_types: Type[Exception]) -> None:
        """Do not send these exception types to GlitchTip / Sentry — a `ClientError` or a
        `DomainError` already is not."""
        ...

    def report_sentry_exceptions(self, *exc_types: Type[Exception]) -> None:
        """Send these exception types to GlitchTip / Sentry although they are expected."""
        ...

    def with_trace(
        self,
        trace_id: Optional[str] = None,
        span_id: Optional[str] = None,
        carrier: Optional[Mapping[str, Any]] = None,
    ) -> FrameworkSpanContext:
        """Open a trace around a `with` block — every use case run inside is a span of it; the
        parent is a `carrier`'s `traceparent`, else `trace_id`/`span_id`, else a new root."""
        ...

    def with_parent_trace(self) -> FrameworkSpanContext:
        """Adopt the currently active OTel span for log correlation (no new span created)."""
        ...

    def context(
        self,
        context_to_set: Mapping[Any, Any] | None = None,
        global_scope: bool = False,
        kind: EntrypointKind | None = None,
    ) -> FrameworkContext:
        """A scope of this bus for a `with` block: every execution inside reads its values,
        nothing outside does — `global_scope=True` for every execution of this bus, `kind` for
        the entrance that opened the flow.

            with billing.context({"tenant_id": "acme", "user_id": "ana"}):
                billing(CommandIssueInvoice(total=100))
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

    def store_context_in(
        self,
        store: ContextStore,
        ttl: timedelta = ...,
        every: timedelta = ...,
    ) -> None:
        """Share this bus's context through `store`, and store there what the bus stores beside
        it — drafts, pipeline runs, leases, `cache`, `idempotency`. The API does not change;
        every node of its executions is retained for `ttl`, what others wrote is read again at
        most once per `every`.

            billing.store_context_in(KeyValueContexts(RedisKeyValue(redis)))
        """
        ...

    def store(self) -> ContextStore:
        """Where this bounded context stores what it stores: its own store
        (`bus.store_context_in`), else the process's (`store_context_in`), else this process's
        memory."""
        ...

    @property
    def cache(self) -> CacheFacade:
        """This bounded context's cache, stored where its context is stored — `@billing.cache`,
        `@billing.cache.ttl(...)`, `.sliding(...)`, `.versioned(...)`, or handed to an
        adapter.
        """
        ...

    @property
    def idempotency(self) -> IdempotencyFacade:
        """This bounded context's idempotency, stored where its context is stored —
        `@billing.idempotency` on a Feature or an ApplicationService."""
        ...

    @property
    def logger(self) -> LoggerProxy:
        """The bounded context's logger — created on first use, named after the bus."""
        ...
