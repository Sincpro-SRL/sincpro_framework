from functools import partial
from typing import Any, Dict, Generic, Mapping, Optional, Type, cast

from sincpro_log.logger import LoggerProxy, create_logger

from . import ioc
from .aio import AsyncBus
from .bus import FrameworkBus
from .context.framework_context import FrameworkContext
from .context.mixin import ContextMixin
from .deps import DependencyLocator, TDeps
from .error_handler import ErrorHandler, build_error_handler_chain
from .exceptions import DependencyAlreadyRegistered, SincproFrameworkNotBuilt
from .middleware import Middleware, MiddlewarePipeline
from .observability import FrameworkSpanContext, Observability
from .sincpro_abstractions import TypeDTO, TypeDTOResponse


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
        """
        # Logger
        self._is_logger_configured: bool = False
        self._logger_name: str = bundled_context_name
        self._logger: LoggerProxy | None = None
        self.log_after_execution: bool = log_after_execution
        self.log_app_services: bool = log_app_services
        self.log_features: bool = log_features
        self.observability = Observability(bundled_context_name, package=package or "")

        self._init_context_storage()

        # Container
        self._sp_container = ioc.FrameworkContainer(logger_bus=self.logger)  # type: ignore[call-arg]
        self._sp_container.logger_bus = self.logger  # type: ignore[assignment]

        # Decorators
        self.feature = partial(ioc.inject_feature_to_bus, self._sp_container)
        self.app_service = partial(ioc.inject_app_service_to_bus, self._sp_container)

        # Registry for dynamic dep injection
        self.dynamic_dep_registry: Dict[str, Any] = dict()
        self._deps_locator = DependencyLocator(self.dynamic_dep_registry)

        # Error handlers — ordered pipeline (first registered, first executed)
        self._global_error_handlers: list[ErrorHandler] = []
        self._feature_error_handlers: list[ErrorHandler] = []
        self._app_service_error_handlers: list[ErrorHandler] = []
        self.global_error_handler: Optional[ErrorHandler] = None
        self.feature_error_handler: Optional[ErrorHandler] = None
        self.app_service_error_handler: Optional[ErrorHandler] = None

        # Middleware pipeline
        self.middleware_pipeline = MiddlewarePipeline()

        self.was_initialized: bool = False
        self.bus: FrameworkBus | None = None

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

    def build_root_bus(self):
        """Build the root bus with the dependencies provided by the user"""
        self._add_dependencies_provided_by_user()
        self._add_error_handlers_provided_by_user()
        self.was_initialized = True
        dto_registry = self._sp_container.dto_registry()

        self.bus = self._sp_container.framework_bus()  # type: ignore[assignment]
        self._bind_context_to_handlers()

        # Set the loggers
        self.bus.log_after_execution = self.log_after_execution
        self.bus.feature_bus.log_after_execution = (
            self.log_after_execution and self.log_features
        )
        self.bus.app_service_bus.log_after_execution = (
            self.log_after_execution and self.log_app_services
        )

        # One observability object for the three buses: same identity, same status.
        self.bus.feature_bus.observability = self.observability
        self.bus.app_service_bus.observability = self.observability
        self.bus.observability = self.observability

        # Set the DTO registry Tricky way but it works
        self.bus.dto_registry = dto_registry

        self.observability.start(self.logger)

    def add_dependency(self, name, dep: Any):
        """
        Add a dependency to the framework where
        The Feature and App Service have as attribute

        Call this during startup, before the first execution — not
        concurrently with in-flight requests. `dynamic_dep_registry` is a
        plain dict with no lock: under the GIL, a late/concurrent call is at
        worst "last write wins"; without it (a free-threaded build; see
        `ioc.py` for why that's not in scope yet for this codebase's other
        dependencies anyway) it becomes a genuine data race. Not enforced
        today to avoid a breaking change — documented so it isn't a surprise
        later.
        """
        if name in self.dynamic_dep_registry:
            error = DependencyAlreadyRegistered(f"The dependency {name} is already injected")
            self.observability.record_error(error, "", "framework", kind="framework")
            raise error
        self.dynamic_dep_registry[name] = dep

    @property
    def deps(self) -> TDeps:
        """Read-only locator of dependencies registered with ``add_dependency``.

        Inside a Feature / ApplicationService keep using ``self.<name>``.
        Use this from the bounded-context root (SDK caller, test, entrypoint)::

            adapter = framework.deps.my_adapter
        """
        return cast(TDeps, self._deps_locator)

    def add_middleware(self, middleware: Middleware):
        """Add middleware function to the execution pipeline"""
        self.middleware_pipeline.add_middleware(middleware)

    def add_global_error_handler(self, handler: ErrorHandler):
        """Register a global error handler.

        Signature: ``(error) -> Any``.
        If the handler re-raises, the framework calls the next handler in the chain.
        First registered = first to execute.

        Example::

            def base(error):
                return ErrorResponse(str(error))

            app.add_global_error_handler(base)

            def logger(error):
                log.error(error)
                raise error  # delegates to base

            app.add_global_error_handler(logger)
        """
        if not callable(handler):
            raise TypeError("The handler must be a callable")
        self._global_error_handlers.append(handler)
        self.global_error_handler = build_error_handler_chain(self._global_error_handlers)
        if self.was_initialized and self.bus is not None:
            self.bus.handle_error = self.global_error_handler

    def add_feature_error_handler(self, handler: ErrorHandler):
        """Register a feature-level error handler.

        Same semantics as ``add_global_error_handler``.
        """
        if not callable(handler):
            raise TypeError("The handler must be a callable")
        self._feature_error_handlers.append(handler)
        self.feature_error_handler = build_error_handler_chain(self._feature_error_handlers)
        if self.was_initialized and self.bus is not None:
            self.bus.feature_bus.handle_error = self.feature_error_handler

    def add_app_service_error_handler(self, handler: ErrorHandler):
        """Register an app service-level error handler.

        Same semantics as ``add_global_error_handler``.
        """
        if not callable(handler):
            raise TypeError("The handler must be a callable")
        self._app_service_error_handlers.append(handler)
        self.app_service_error_handler = build_error_handler_chain(
            self._app_service_error_handlers
        )
        if self.was_initialized and self.bus is not None:
            self.bus.app_service_bus.handle_error = self.app_service_error_handler

    def ignore_sentry_exceptions(self, *exc_types: Type[Exception]) -> None:
        """Do not send these exception types to GlitchTip / Sentry.

        Error handlers still run. Use this for expected domain errors
        (validation, insufficient funds, etc.) so they do not hide real bugs:
        anything not in this list is reported even if a handler swallows it.
        """
        self.observability.ignore(*exc_types)

    def context(
        self, context_to_set: Mapping[str, Any], global_scope: bool = False
    ) -> FrameworkContext:
        """
        Create a context manager with the specified attributes.

        Default is isolated per task/thread. Concurrent executions of this
        instance do not see each other's keys. Pass global_scope=True to
        publish keys on this instance so concurrent executions can read them.

        Args:
            context_to_set: Dictionary of context attributes to set
            global_scope: Publish on the instance instead of isolating the task

        Returns:
            FrameworkContext instance ready to be used with 'with' statement

        Example:
            with app.context({"correlation_id": "123", "user_id": "456"}) as app_with_context:
                result = app_with_context(some_dto)
        """
        return FrameworkContext(cast(Any, self), context_to_set, global_scope)

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
                with traced.context({"user.id": "u-123"}) as app_with_ctx:
                    result = app_with_ctx(MyDTO(...))
        """
        if not self.was_initialized:
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
        if not self.was_initialized:
            self.build_root_bus()
        return self.observability.trace_context(cast(Any, self), adopt_active=True)

    def get_async_bus(self) -> AsyncBus:
        """Return a stateless async facade over this framework's bus.

        Builds the root bus if it wasn't built yet (same lazy behavior as
        ``__call__``). Use this from a caller that is itself ``async def`` and
        wants to fan out several DTOs concurrently, e.g. via ``asyncio.gather``,
        without blocking its event loop. See ``Bus.get_async_bus`` /
        ``AsyncBus`` for the propagation and reuse semantics.
        """
        if not self.was_initialized:
            self.build_root_bus()
        assert self.bus is not None
        return self.bus.get_async_bus()

    def __call__(
        self, dto: TypeDTO, return_type: Type[TypeDTOResponse] | None = None
    ) -> TypeDTOResponse | None:
        """Main function to execute the framework"""
        if not self.was_initialized:
            self.build_root_bus()

        if self.bus is None:
            error = SincproFrameworkNotBuilt(
                "Check the decorators are rigistering the features and app services, check the imports of each "
                "feature and app service"
            )
            self.observability.record_error(error, "", "framework", kind="framework")
            raise error

        implicit_token = None
        implicit_overlay = None
        if self._overlay_var.get() is None and not self._in_global_var.get():
            implicit_token, implicit_overlay = self._push_overlay(dict(self._shared_context))

        def executor(processed_dto, **exec_kwargs) -> TypeDTOResponse | None:
            assert (
                self.bus is not None
            )  # Help mypy understand this is safe after the check above

            return self.bus.execute(processed_dto)

        try:
            return self.middleware_pipeline.execute(dto, executor, return_type=return_type)
        finally:
            if implicit_token is not None and implicit_overlay is not None:
                self._pop_overlay(implicit_token, implicit_overlay)

    @property
    def logger(self) -> LoggerProxy:
        """Get the bundle context logger."""
        if not self._is_logger_configured:
            self._logger = create_logger(self._logger_name)
            self._is_logger_configured = True
        return self._logger  # type: ignore[return-value]
