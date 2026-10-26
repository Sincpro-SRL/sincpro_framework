"""The two doors to observability.

``Observability`` — one per ``UseFramework``. It knows who the bus is, brings the
backends up, and is the only object ``bus.py`` and ``use_bus.py`` talk to, so the
rest of the framework never imports opentelemetry, sentry_sdk, or anything under
``observability.errors`` / ``observability.tracing``.

``process`` — the transport around those buses. A bus is instrumented for you; the
ASGI request, the httpx call, the server's own loggers and a 500 that dies before any
Feature runs belong to the **process**, and must not be exported under a bus's name.

Nothing here raises and nothing here is required: with neither extra installed every
method is a no-op and the bus runs exactly as it would without observability.

How the bus drives it — one execution, as ``bus.py`` does it:

    observability = Observability("billing", "billing_sdk")
    observability.start(logger)                       ->  ObservabilityStatus(…)
    with observability.execution() as outermost:      # True only for the outermost bus
        with observability.span("CreateInvoice", "feature") as span:
            with observability.measure(dto, feature, "feature") as measured:
                try:
                    response = feature.execute(dto)
                except Exception as error:
                    measured.failed(error)
                    observability.failed(error, dto, feature, "feature", span)
                    raise

How a transport uses ``process``:

    tracer = process.tracer("sincpro.http")           # None without opentelemetry
    process.bind_logger(access_logger)                # access lines carry the trace_id
    if not process.was_reported(error):
        process.record_error(error)                   # the process release, not a bus
"""

from collections.abc import Generator, Mapping
from contextlib import contextmanager
from typing import Any, ContextManager, Optional, Tuple, Type

from sincpro_log.logger import LoggerProxy, create_logger

from sincpro_framework.common.failures.classification import is_fault
from sincpro_framework.common.naming import registered_name
from sincpro_framework.observability import correlation, failure
from sincpro_framework.observability.domain import (
    FAULT,
    OK,
    OUTCOME,
    ComponentStatus,
    ObservabilityIdentity,
    ObservabilityStatus,
    caller_module,
    off,
    on,
    resolve_identity,
)
from sincpro_framework.observability.errors.record_error import ErrorKind, record_error
from sincpro_framework.observability.errors.setup import setup_errors
from sincpro_framework.observability.metrics import metrics
from sincpro_framework.observability.registry import PROCESS, observability_registry
from sincpro_framework.observability.tracing.attributes import describe
from sincpro_framework.observability.tracing.setup import (
    OTEL_AVAILABLE,
    current_otel_context,
    host_provider_is_real,
    setup_tracing,
)
from sincpro_framework.observability.tracing.span_context import FrameworkSpanContext
from sincpro_framework.observability.tracing.span_error import (
    span_attributes,
    span_correlation,
    span_error,
    span_in_error,
)
from sincpro_framework.observability.tracing.span_execution import span_execution

IgnoredExceptions = Tuple[Type[Exception], ...]


class Observability:
    """Who this bus is, which backends came up, and how it reports what happens."""

    def __init__(self, bus: str = "", package: str = "", version: str = "") -> None:
        """Store who the bus is — `bus`, its `package` and `version` — without resolving it.

            Observability("billing", "billing_sdk").bus_name   ->  "billing"
            Observability("billing").status.otel.reason         ->  "not_built"
            Observability().logger                              ->  logger "sincpro_framework"

        Context: the identity is resolved lazily (`identity`), but the module that builds the
        bus is read here, while it is still on the stack. Nothing is reported or ignored yet
        and the logger is the bus's own until `start` hands another one.
        """
        self._bus = bus
        self._package = package
        self._version = version
        # Read here, not on first use: at this point the stack still holds the
        # library that is building the bus. From a request it would name the host.
        self._module_name = caller_module()
        self._identity: Optional[ObservabilityIdentity] = None
        self.status: ObservabilityStatus = ObservabilityStatus()
        self.ignored_errors: IgnoredExceptions = ()
        self.reported_errors: IgnoredExceptions = ()
        self.logger: LoggerProxy = create_logger(bus or "sincpro_framework")

    @property
    def bus_name(self) -> str:
        """The bounded context this bus is — what its executions name as theirs."""
        return self._bus

    @property
    def identity(self) -> ObservabilityIdentity:
        """Resolved once, on first use — the distribution lookup behind it is not free."""
        if self._identity is None:
            self._identity = resolve_identity(
                self._bus, self._package, self._version, self._module_name
            )
        return self._identity

    def start(self, logger: Optional[LoggerProxy] = None) -> ObservabilityStatus:
        """Bring both backends up for this bus. Each one degrades on its own.

            observability.start(logger)
                ->  ObservabilityStatus(sentry=off("dsn_missing"), otel=off("no_endpoint"))
                    logged as  "observability sentry=off:dsn_missing otel=off:no_endpoint"

        One line says how both came up — a `warning` when either `failed`, `info` otherwise;
        called without a `logger`, the status is stored and nothing is logged.
        """
        if logger is not None:
            self.logger = logger
        self.status = ObservabilityStatus(
            sentry=setup_errors(self.identity),
            otel=setup_tracing(self.identity, logger),
        )
        message = (
            f"observability sentry={self.status.sentry.state}:{self.status.sentry.reason} "
            f"otel={self.status.otel.state}:{self.status.otel.reason}"
        )
        try:
            if "failed" in (self.status.sentry.state, self.status.otel.state):
                logger.warning(message)  # type: ignore[union-attr]
            else:
                logger.info(message)  # type: ignore[union-attr]
        except Exception:
            pass
        return self.status

    def log_identity(self) -> dict[str, str]:
        """A log context source: the service, version and tenant on every line of this bus.

            log_identity()   ->  {}                   # nothing has resolved the identity yet
            log_identity()   ->  {"service_name": "billing-sdk", "service_version": "1.2.0",
                                  "release": "billing-sdk:1.2.0", "tenant": "acme"}

        Context: it never resolves the identity itself — a first line logged at import would
        resolve it before a project hands its `APP_RELEASE` over, and cache the wrong name. It
        reads the identity once `start` (or a span) has resolved it; the tenant is read on each
        line. Never raises."""
        try:
            if self._identity is None:
                return {}
            return correlation.log_fields(self._identity)
        except Exception:
            return {}

    def ignore(self, *error_types: Type[Exception]) -> None:
        """Exclude these exception types from GlitchTip. They are expected, not bugs.

            observability.ignore(InvoiceLocked, InvoiceLocked)
            observability.ignored_errors   ->  (InvoiceLocked,)

        Added to what was ignored before, in order; a type already there is not repeated.
        """
        known = list(self.ignored_errors)
        for error_type in error_types:
            if error_type not in known:
                known.append(error_type)
        self.ignored_errors = tuple(known)

    def report(self, *error_types: Type[Exception]) -> None:
        """Send these exception types to GlitchTip although they are expected — a
        `ClientError` or a `DomainError` a project wants to see.

            observability.report(InvoiceNotFound)
            observability.reported_errors          ->  (InvoiceNotFound,)
            observability.faulted(InvoiceNotFound())   ->  True

        Added to what was reported before, in order; a type already there is not repeated.
        """
        known = list(self.reported_errors)
        for error_type in error_types:
            if error_type not in known:
                known.append(error_type)
        self.reported_errors = tuple(known)

    def faulted(self, error: BaseException) -> bool:
        """Whether `error` is a problem of the service, as this bus judges it — the one answer
        every signal reads (`common.failures.is_fault`, with what the bus ignores and reports):
        GlitchTip, an `error` line with its traceback, a span in error, `sincpro.fault="true"`.
        Anything else — something not found, refused, invalid — is an answer: an `info` line.
        """
        return is_fault(error, self.ignored_errors, self.reported_errors)

    @contextmanager
    def span(
        self, dto_name: str, layer: str, use_case: str = ""
    ) -> Generator[Any, None, None]:
        """Span for one DTO execution, with its ids and coordinates bound to the logger. It
        starts `ok`; `failed` replaces the outcome with the failure's, as the metrics do.

            with observability.span("CreateInvoice", "feature", "billing.CreateInvoice"):
                ...    ->  span "billing/CreateInvoice": sincpro.outcome="ok",
                           sincpro.fault="false", the tenant, the user and the context keys

        It yields `None` without a tracer; a fault escaping the block sets the status ERROR.

        Context: the execution is this bus's for as long as the span is open, so every signal
        recorded inside reads this bus's context (PRD_03 §4.10). The context goes on the span
        when it closes — what an interceptor, a hook or `execute` set midway included.
        `use_case` is the use case's identity (`registered_name`), `dto_name` when not given.
        """
        with (
            correlation.running(self),
            span_execution(
                dto_name,
                layer,
                self.identity.bus,
                self.logger,
                {OUTCOME: OK, FAULT: "false"},
                use_case,
            ) as span,
        ):
            try:
                yield span
            except BaseException as error:
                if self.faulted(error):
                    span_in_error(span, error)
                raise
            finally:
                if span is not None:
                    span_correlation(span, correlation.execution_keys())

    # -----------------------------------------------------------------------------------------
    # One failure, reported once
    # -----------------------------------------------------------------------------------------

    def measure(self, dto: object, handler: object, layer: str) -> ContextManager[Any]:
        """The metrics of one DTO execution: its duration by outcome, and what its use case
        declares — `answered(response)` and `failed(error)` on what it yields."""
        return metrics.measured(self._bus, dto, handler, layer, who=self)

    def _judged(self, error: BaseException) -> dict[str, str]:
        """`sincpro.outcome` — the failure's kind — and `sincpro.fault`, as the metrics carry
        them, so the span, the log line and GlitchTip say the same."""
        try:
            judged = metrics.outcome_of(error, self.faulted(error))
            return {OUTCOME: judged[OUTCOME], FAULT: judged[FAULT]}
        except Exception:
            return {OUTCOME: "internal", FAULT: "true"}

    def record_error(
        self,
        error: Exception,
        dto_name: str,
        layer: str,
        span: Any = None,
        kind: ErrorKind = "instance",
    ) -> None:
        """Report a failure everywhere it belongs: on the span and in GlitchTip."""
        judged = self._judged(error)
        span_attributes(span, judged)
        span_error(span, error, None)
        record_error(
            error,
            dto_name,
            layer,
            self.identity,
            kind=kind,
            ignored_exceptions=self.ignored_errors,
            outcome=judged[OUTCOME],
            reported_exceptions=self.reported_errors,
        )

    def _describe(self, error: BaseException, dto: object) -> tuple[str, dict[str, Any]]:
        """The summary and the fields of the one log line a failure gets.

        Context: what a handler recorded (`failure.remember`) names the handler, the chain and
        where it failed; an error no handler recorded — an unknown DTO, a failure in the bus —
        is described from the DTO alone. Both carry the outcome and the fault as judged here.

            _describe(KeyError("x"), CreateInvoice(...))
                ->  ("CreateInvoice failed: KeyError: 'x'",
                     {"dto": ..., "sincpro_fault": ...})

        1. No recorded failure leads to `error`: the DTO, the error type and the use case.
        2. A recorded one: its own fields, plus `raised_as` when an error handler mapped the
           original error to another type.
        Final: (summary, fields).
        """
        recorded = failure.failure_of(error)
        if recorded is None:
            fields = {
                "dto": repr(dto),
                "error_type": type(error).__name__,
                "sincpro_use_case": registered_name(type(dto), self._bus),
                "sincpro_outcome": self._judged(error)[OUTCOME],
                "sincpro_fault": self._judged(error)[FAULT],
            }
            return f"{type(dto).__name__} failed: {type(error).__name__}: {error}", fields
        fields = recorded.fields()
        fields["sincpro_outcome"] = self._judged(error)[OUTCOME]
        fields["sincpro_fault"] = self._judged(error)[FAULT]
        if error is not recorded.error:
            fields["raised_as"] = type(error).__name__
        return recorded.summary, fields

    def annotate(self, span: Any, attributes: Mapping[str, str]) -> None:
        """Extra attributes on the span of one execution — who handled it, what it replaced."""
        span_attributes(span, attributes)

    def describe(self, span: Any, handler: object, source: object) -> None:
        """What the handler's use case declared with `traces.attributes`, read off `source` —
        its Command before it runs, its Response after a success. Never raises."""
        if span is None:
            return
        describe(span, type(handler), source)

    def execution(self) -> ContextManager[bool]:
        """The block around one dispatch; yields whether this bus is the outermost one — the
        only one that logs an error escaping it (`escaped`)."""
        return failure.execution()

    def handling(self, dto_name: str) -> ContextManager[None]:
        """The block a handler runs in; `dto_name` joins the chain a failure reports
        (`ApplyPayment → CreateInvoice`)."""
        return failure.handling(dto_name)

    def failed(
        self, error: Exception, dto: object, handler: object, layer: str, span: Any
    ) -> None:
        """Tell a use case failing with `error` to its span, the failure record and GlitchTip.

            observability.failed(KeyError("x"), CreateInvoice(...), feature, "feature", span)
                ->  span: sincpro.outcome="internal", sincpro.fault="true", the exception
                    and code.* where it failed; one GlitchTip event
            observability.failed(ClientError("no stock"), ...)
                ->  span: sincpro.outcome="invalid", sincpro.fault="false"; no GlitchTip event

        Context: the handler that raised describes the failure, puts it on its span and
        sends it to GlitchTip. An outer handler the same error crosses adds nothing but its
        span's outcome — every span it crosses takes one, as every run's metric does."""
        judged = self._judged(error)
        span_attributes(span, judged)
        self.describe(span, handler, None)
        recorded = failure.remember(
            error, dto, self._bus, handler, layer, trace_ids=current_otel_context()
        )
        if recorded is None:
            return
        span_error(span, error, recorded.error_at)
        record_error(
            error,
            type(dto).__name__,
            layer,
            self.identity,
            ignored_exceptions=self.ignored_errors,
            details={**self.logger.logger_fields, **recorded.fields()},
            outcome=judged[OUTCOME],
            use_case=registered_name(type(dto), self._bus),
            reported_exceptions=self.reported_errors,
        )

    def escaped(self, error: BaseException, dto: object) -> None:
        """Log the error that leaves the whole call — the one log line a failure gets.

            observability.escaped(KeyError("x"), CreateInvoice(...))
                ->  error  "CreateInvoiceFeature failed: KeyError: 'x'"  with its traceback
            observability.escaped(ClientError("no stock"), CreateInvoice(...))
                ->  info   "CreateInvoiceFeature failed: ClientError: no stock"

        Context: called by the outermost bus only, so an error is logged once. A failure that
        is not a fault — something not found, refused, invalid — is an answer: an `info` line,
        no traceback. A fault is an `error` line with it."""
        recorded = failure.failure_of(error)
        if recorded is not None:
            failure.annotate(error, recorded)
        summary, fields = self._describe(error, dto)
        if not self.faulted(error) or (recorded and not self.faulted(recorded.error)):
            self.logger.info(summary, **fields)
        else:
            self.logger.error(summary, exc_info=error, **fields)

    def handled(self, error: BaseException, dto: object) -> None:
        """Log an error an error handler answered — `info` unless it is a fault.

            observability.handled(ClientError("no stock"), CreateInvoice(...))
                ->  info  "... failed: ClientError: no stock (answered by an error handler)"
            observability.handled(KeyError("x"), CreateInvoice(...))
                ->  warning  "... failed: KeyError: 'x' (answered by an error handler)"

        Context: an error handler answered instead of raising; the caller never sees the
        error, so this line is its only trace."""
        summary, fields = self._describe(error, dto)
        message = f"{summary} (answered by an error handler)"
        if not self.faulted(error):
            self.logger.info(message, **fields)
        else:
            self.logger.warning(message, **fields)

    def trace_context(
        self,
        framework: Any,
        trace_id: Optional[str] = None,
        span_id: Optional[str] = None,
        carrier: Optional[Any] = None,
        adopt_active: bool = False,
    ) -> FrameworkSpanContext:
        """The ``with_trace()`` / ``with_parent_trace()`` block for this bus."""
        return FrameworkSpanContext(
            framework,
            self.identity,
            trace_id=trace_id,
            span_id=span_id,
            carrier=carrier,
            adopt_active=adopt_active,
        )


class ProcessObservability:
    """What the transport needs, and nothing a bounded context already has."""

    @property
    def identity(self) -> ObservabilityIdentity:
        """Who this process is: ``artifact:version``, without a bus segment.

        Prefers what a bus already announced, so the transport and the buses of one
        deployment never disagree about the version that is running.

            process.identity   ->  ObservabilityIdentity(
                                   artifact="registry.example.com/team/sincpro-odoo:18.5.0",
                                   version="", bus="")

        Nothing announced: resolved from the caller's module, its bus emptied.
        """
        announced = observability_registry.process_identity()
        if announced is not None:
            return announced
        resolved = resolve_identity("", module_name=caller_module())
        return resolved.model_copy(update={"bus": ""})

    @property
    def status(self) -> ComponentStatus:
        """Who owns the global provider that transport instrumentation will find.

        ``on:installed`` — this framework put the process provider there.
        ``on:host`` — someone got there first (Odoo, an operator's auto-instrumentation).
        ``off:*`` — nothing is registered, so transport spans would be no-ops.

            process.status   ->  on("installed")     # active=True, state="on"
            process.status   ->  off("no_endpoint")   # active=False, state="off"

        ``sdk_missing`` without opentelemetry, before anything else is asked.
        """
        if not OTEL_AVAILABLE:
            return off("sdk_missing")
        if observability_registry.tracer_provider(PROCESS) is not None:
            return on("installed")
        if host_provider_is_real():
            return on("host")
        return off("no_endpoint")

    def tracer(self, instrumentation_name: str) -> Optional[Any]:
        """Tracer for a transport span. The global one: this process, or the host.

        Returns ``None`` when opentelemetry is not installed, so a caller can skip
        instrumenting instead of guarding the import itself.
        """
        try:
            from opentelemetry import trace

            return trace.get_tracer(instrumentation_name)
        except Exception:
            return None

    def current_span(self) -> Optional[Any]:
        """The OpenTelemetry span running right now — to add an event or an attribute to it —
        or ``None`` when nothing is tracing or opentelemetry is not installed. Never raises.

            process.current_span()   ->  the request's span, inside a traced ASGI request
            process.current_span()   ->  None, at import time — no span is active

        A span whose context is not valid (OTel's placeholder) counts as none.
        """
        try:
            from opentelemetry import trace

            span = trace.get_current_span()
            return span if span.get_span_context().is_valid else None
        except Exception:
            return None

    def span_failed(self, span: Any, error: BaseException) -> None:
        """A span a caller opened itself (`tracer(...).start_span`) ends in `error`: the
        exception recorded, `error.type` set as the metrics and the logs spell it, the status
        ERROR. Nothing without a span or without opentelemetry. Never raises.

            span = process.tracer("sincpro.http").start_span("GET /invoices")
            process.span_failed(span, TimeoutError("upstream"))
                ->  an exception event, error.type="TimeoutError",
                    Status(StatusCode.ERROR, "TimeoutError")

        Ending the span stays the caller's: this only marks it.
        """
        if span is None:
            return
        try:
            from opentelemetry.trace import Status, StatusCode

            span.record_exception(error)
            span.set_attribute("error.type", type(error).__name__)
            span.set_status(Status(StatusCode.ERROR, type(error).__name__))
        except Exception:
            return

    def trace_ids(self) -> dict:
        """``trace_id``/``span_id`` of the active span, for a process logger.

        Hand it to ``logger.add_context_source(...)`` so an access log line, or a
        line from uvicorn, lands on the same trace as the request that caused it.
        """
        return current_otel_context()

    def bind_logger(self, logger: Any) -> None:
        """Make a process logger stamp the ids of the request being served.

        Without this an access log line, or a line from uvicorn, has no trace_id and
        cannot be found from the trace it belongs to. Never raises: a logger that
        does not support it simply goes on logging.
        """
        try:
            logger.add_context_source(self.trace_ids)
        except Exception:
            return

    def was_reported(self, error: BaseException) -> bool:
        """Whether a bus already logged ``error`` on its way out.

        A transport that catches what a bus raised answers the caller, but logging it again
        would put the same failure in the logs twice. What no bus saw — a malformed
        request, a failure before any Feature ran — is still the transport's to log.
        """
        return failure.was_reported(error)

    def record_error(self, error: Exception, layer: str = "process") -> None:
        """Report a transport failure under the process release. Never raises."""
        record_error(error, "", layer, self.identity, kind="instance")


process = ProcessObservability()
