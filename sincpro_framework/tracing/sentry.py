"""GlitchTip / Sentry error reporting — optional, silent if unused.

Independent from the host (Odoo, FastAPI):

- Never calls ``sentry_sdk.init()`` — that would overwrite the host client
- Uses an isolated ``Client`` whose ``release`` is the app release computed
  at ``UseFramework`` init (``{app_name}:{library_version}``), not env vars
- DSN comes from framework conf (``SENTRY_PYTHON_DSN``)
- Does not mark exceptions; if Odoo also captures, that is a second event
  with Odoo's release. That is intended.

Missing ``sentry-sdk`` or missing DSN → no-op, never raises.
"""

import inspect
import os
from importlib.metadata import packages_distributions, version
from typing import Any, Dict, Literal, Mapping, Optional, Tuple, Type

from ..sincpro_conf import settings
from .status import ComponentStatus, component_status, status_from_exception

FRAMEWORK_DISTRIBUTION = "sincpro-framework"
UNKNOWN_VERSION = "unknown"

SentryKind = Literal["instance", "framework"]
IgnoredExceptions = Tuple[Type[Exception], ...]

_import_to_dist: Optional[Mapping[str, list[str]]] = None
_clients: Dict[str, Any] = {}
_app_releases: Dict[str, str] = {}

try:
    import sentry_sdk  # pyright: ignore[reportMissingImports]

    _SENTRY_SDK_AVAILABLE = True
except ImportError:
    _SENTRY_SDK_AVAILABLE = False


def _is_framework_module(module_name: str) -> bool:
    return module_name == "sincpro_framework" or module_name.startswith("sincpro_framework.")


def library_version(distribution_name: str) -> str:
    """Return the installed version of a distribution, or ``unknown``."""
    try:
        return version(distribution_name)
    except Exception:
        return UNKNOWN_VERSION


def _import_name_to_distributions() -> Mapping[str, list[str]]:
    global _import_to_dist
    if _import_to_dist is None:
        try:
            _import_to_dist = packages_distributions()
        except Exception:
            _import_to_dist = {}
    return _import_to_dist


def version_for_import_name(module_name: str) -> str:
    """Resolve an import name (top-level package) to an installed dist version."""
    top = module_name.split(".")[0]
    dist_names = _import_name_to_distributions().get(top) or [top.replace("_", "-")]
    for dist_name in dist_names:
        resolved = library_version(dist_name)
        if resolved != UNKNOWN_VERSION:
            return resolved
    return UNKNOWN_VERSION


def detect_caller_library_version() -> str:
    """Version of the first caller outside ``sincpro_framework``.

    Used so ``UseFramework("payment-cybersource")`` created from
    ``sincpro_payments_sdk`` gets that SDK's Poetry version without the
    instance passing it explicitly.
    """
    try:
        for frame_info in inspect.stack()[1:]:
            module = inspect.getmodule(frame_info.frame)
            if module is None or not module.__name__:
                continue
            if _is_framework_module(module.__name__):
                continue
            return version_for_import_name(module.__name__)
        return library_version(FRAMEWORK_DISTRIBUTION)
    except Exception:
        return UNKNOWN_VERSION


def build_release(app_name: str, lib_version: str) -> str:
    """Build a GlitchTip/Sentry release: ``{app_name}:{library_version}``."""
    name = (app_name or FRAMEWORK_DISTRIBUTION).strip() or FRAMEWORK_DISTRIBUTION
    resolved_version = (lib_version or UNKNOWN_VERSION).strip() or UNKNOWN_VERSION
    return f"{name}:{resolved_version}"


def framework_release() -> str:
    """Release for errors that belong to the framework itself."""
    return build_release(FRAMEWORK_DISTRIBUTION, library_version(FRAMEWORK_DISTRIBUTION))


def instance_release(app_name: str, lib_version: str) -> str:
    """Release for errors raised inside a UseFramework instance."""
    return build_release(app_name, lib_version)


def register_app_release(app_name: str, release: str) -> None:
    """Store the app release computed at UseFramework init. Used on every event."""
    if app_name and release:
        _app_releases[app_name] = release


def _tenant() -> str:
    return (os.environ.get("TENANT") or "").strip()


def _dsn() -> str:
    """DSN from framework conf (``SENTRY_PYTHON_DSN``). Empty if not usable."""
    try:
        raw = (settings.sentry_dsn or "").strip()
    except Exception:
        return ""
    if raw.startswith(("http://", "https://")) and "@" in raw:
        return raw
    return ""


def _isolated_client(release: str) -> Optional[Any]:
    """Client owned by the framework. Does not touch the host global client."""
    if not _SENTRY_SDK_AVAILABLE:
        return None
    dsn = _dsn()
    if not dsn or not release:
        return None
    tenant = _tenant()
    cached = _clients.get(release)
    if cached is not None:
        return cached
    import sentry_sdk  # pyright: ignore[reportMissingImports]

    client_kwargs: Dict[str, Any] = {
        "dsn": dsn,
        "release": release,
        "traces_sample_rate": 0.0,
        "auto_enabling_integrations": False,
        "send_default_pii": False,
    }
    if tenant:
        client_kwargs["environment"] = tenant
    client = sentry_sdk.Client(**client_kwargs)
    _clients[release] = client
    return client


def setup_sentry(service_name: str, release: str = "") -> ComponentStatus:
    """Prepare an isolated Glitch client for this instance. Never raises.

    Does not call ``sentry_sdk.init()``. Missing SDK or DSN is ``off``.
    ``release`` is the app release registered at UseFramework init.
    """
    try:
        if not _SENTRY_SDK_AVAILABLE:
            return component_status(False, "off", "sdk_missing")
        if not _dsn():
            return component_status(False, "off", "dsn_missing")
        event_release = release or _app_releases.get(service_name) or ""
        if not event_release:
            event_release = instance_release(service_name, UNKNOWN_VERSION)
        register_app_release(service_name, event_release)
        client = _isolated_client(event_release)
        if client is None:
            return component_status(False, "failed", "client_not_created")
        return component_status(True, "on", "init")
    except Exception as exc:
        return status_from_exception(exc)


def record_sentry_error(
    error: Exception,
    dto_name: str,
    layer: str,
    instance: str,
    kind: SentryKind = "instance",
    release: str = "",
    ignored_exceptions: IgnoredExceptions = (),
) -> None:
    """Capture on the isolated framework client. Never raises.

    Does not mark the exception. The host (Odoo) may capture the same
    object with its own release — that is a separate product event.
    """
    try:
        if not _SENTRY_SDK_AVAILABLE:
            return
        if (
            kind == "instance"
            and ignored_exceptions
            and isinstance(error, ignored_exceptions)
        ):
            return

        event_release = release
        if not event_release and instance:
            event_release = _app_releases.get(instance, "")
        if not event_release:
            if kind == "framework":
                event_release = framework_release()
            else:
                event_release = instance_release(instance, UNKNOWN_VERSION)

        import sentry_sdk  # pyright: ignore[reportMissingImports]

        isolation_scope = getattr(sentry_sdk, "isolation_scope", None)
        if isolation_scope is None:
            return
        client = _isolated_client(event_release)
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
            if instance:
                scope.set_tag("sincpro.instance", instance)
            tenant = _tenant()
            if tenant:
                scope.set_tag("tenant", tenant)
            sentry_sdk.capture_exception(error)
    except Exception:
        return
