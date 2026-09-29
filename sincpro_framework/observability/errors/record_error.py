"""Send one exception to GlitchTip. Never raises. Does not touch spans."""

from typing import Any, Literal, Mapping, Optional, Tuple, Type

from sincpro_framework.observability.domain import (
    UNKNOWN,
    ObservabilityIdentity,
    framework_identity,
    tenant,
)
from sincpro_framework.observability.errors import setup as errors_setup
from sincpro_framework.observability.tracing.setup import current_otel_context

ErrorKind = Literal["instance", "framework"]
IgnoredExceptions = Tuple[Type[Exception], ...]


def record_error(
    error: Exception,
    dto_name: str,
    layer: str,
    identity: ObservabilityIdentity,
    kind: ErrorKind = "instance",
    ignored_exceptions: IgnoredExceptions = (),
    details: Optional[Mapping[str, Any]] = None,
    outcome: str = "",
) -> None:
    """Capture on the framework's own client, tagged with who and where.

    ``details`` (handler, DTO chain, where it failed, the execution's context) travel as
    the event's ``sincpro`` context; the handler is also a searchable tag.

    Context: besides the tags the alerts already read (``tenant``, ``sincpro.instance``,
    ``sincpro.dto``, the release), the event carries the keys the spans, the metrics and the
    logs share — ``service_name``, ``sincpro.version``, ``sincpro.context``,
    ``sincpro.use_case``, ``sincpro.outcome``, ``error.type`` — and the active span's
    ``trace_id``, as a tag and as the event's trace context, so an issue opens its trace.

    The host (Odoo) may capture the same exception object with its own release —
    that is a separate product event, and intentionally not suppressed here.
    """
    try:
        if not errors_setup.SDK_AVAILABLE:
            return
        if kind == "instance" and ignored_exceptions:
            if isinstance(error, ignored_exceptions):
                return
        if kind == "framework":
            identity = framework_identity(identity.bus)

        import sentry_sdk  # pyright: ignore[reportMissingImports]

        isolation_scope = getattr(sentry_sdk, "isolation_scope", None)
        if isolation_scope is None:
            return
        client = errors_setup.client_for(identity.release)
        if client is None:
            return

        with isolation_scope() as scope:
            set_client = getattr(scope, "set_client", None)
            if set_client is not None:
                set_client(client)
            scope.set_tag("sincpro.kind", kind)
            scope.set_tag("sincpro.layer", layer)
            if dto_name:
                scope.set_tag("sincpro.dto", dto_name)
            if identity.bus:
                scope.set_tag("sincpro.instance", identity.bus)
            if identity.artifact and identity.artifact != UNKNOWN:
                scope.set_tag("sincpro.package", identity.artifact)
            if details:
                scope.set_context("sincpro", dict(details))
                if details.get("handler"):
                    scope.set_tag("sincpro.handler", details["handler"])
            environment = tenant()
            if environment:
                scope.set_tag("tenant", environment)
            _tag_correlation(scope, error, dto_name, identity, outcome)
            sentry_sdk.capture_exception(error)
    except Exception:
        return


def _tag_correlation(
    scope: Any, error: Exception, dto_name: str, identity: ObservabilityIdentity, outcome: str
) -> None:
    tags = {
        "service_name": identity.service_name,
        "sincpro.version": identity.service_version,
        "sincpro.context": identity.bus,
        "sincpro.use_case": dto_name,
        "sincpro.outcome": outcome,
        "error.type": type(error).__name__,
    }
    ids = current_otel_context()
    if ids:
        tags["trace_id"] = ids["trace_id"]
        # Sentry fills `contexts.trace` with an id of its own only when it is absent.
        scope.set_context("trace", {"trace_id": ids["trace_id"], "span_id": ids["span_id"]})
    for key, value in tags.items():
        if value:
            scope.set_tag(key, value)
