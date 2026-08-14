"""GlitchTip / Sentry error reporting — optional, silent if unused.

Same contract as OTLP in ``provider.py``:

- ``sentry-sdk`` not installed → no-op
- ``SENTRY_DSN`` unset and host did not ``init`` → no-op
- host already called ``sentry_sdk.init`` (Odoo, FastAPI) → do not re-init,
  only capture with sincpro tags

Capture is once per exception object: a Feature error re-raised into an
ApplicationService must not create a second GlitchTip event.
"""

from __future__ import annotations

from ..sincpro_conf import settings

_CAPTURED_ATTR = "_sincpro_sentry_captured"

try:
    import sentry_sdk  # pyright: ignore[reportMissingImports]

    _SENTRY_SDK_AVAILABLE = True
except ImportError:
    _SENTRY_SDK_AVAILABLE = False


def _client_is_active() -> bool:
    if not _SENTRY_SDK_AVAILABLE:
        return False
    import sentry_sdk  # pyright: ignore[reportMissingImports]

    try:
        return bool(sentry_sdk.get_client().is_active())
    except Exception:
        try:
            return sentry_sdk.Hub.current.client is not None
        except Exception:
            return False


def setup_sentry(service_name: str) -> None:
    """Init Sentry when DSN is set and the host has not already initialized it.

    Called from ``UseFramework.build_root_bus()``. Safe to call always.
    Capture still works if the host inited Sentry without ``SENTRY_DSN`` in
    this process env — ``record_sentry_error`` checks the active client.
    """
    if not _SENTRY_SDK_AVAILABLE:
        return

    dsn = settings.sentry_dsn
    if not dsn:
        return

    import sentry_sdk  # pyright: ignore[reportMissingImports]

    if _client_is_active():
        return

    sentry_sdk.init(
        dsn=dsn,
        traces_sample_rate=0.0,
        profiles_sample_rate=0.0,
        auto_session_tracking=False,
        send_default_pii=False,
    )
    sentry_sdk.set_tag("service", service_name)


def record_sentry_error(
    error: Exception,
    dto_name: str,
    layer: str,
    instance: str,
) -> None:
    """Capture an exception if Sentry is active. Never raises. Once per error."""
    if not _SENTRY_SDK_AVAILABLE or not _client_is_active():
        return
    if getattr(error, _CAPTURED_ATTR, False):
        return

    import sentry_sdk  # pyright: ignore[reportMissingImports]

    try:
        with sentry_sdk.new_scope() as scope:
            scope.set_tag("sincpro.layer", layer)
            scope.set_tag("sincpro.dto", dto_name)
            if instance:
                scope.set_tag("sincpro.instance", instance)
            sentry_sdk.capture_exception(error)
        try:
            setattr(error, _CAPTURED_ATTR, True)
        except Exception:
            pass
    except Exception:
        return
