"""Send one exception to GlitchTip. Never raises. Does not touch spans."""

from typing import Literal, Tuple, Type

from sincpro_framework.observability.domain import (
    UNKNOWN,
    ObservabilityIdentity,
    framework_identity,
)
from sincpro_framework.observability.errors import setup as errors_setup

ErrorKind = Literal["instance", "framework"]
IgnoredExceptions = Tuple[Type[Exception], ...]


def record_error(
    error: Exception,
    dto_name: str,
    layer: str,
    identity: ObservabilityIdentity,
    kind: ErrorKind = "instance",
    ignored_exceptions: IgnoredExceptions = (),
) -> None:
    """Capture on the framework's own client, tagged with who and where.

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
            environment = errors_setup.tenant()
            if environment:
                scope.set_tag("tenant", environment)
            sentry_sdk.capture_exception(error)
    except Exception:
        return
