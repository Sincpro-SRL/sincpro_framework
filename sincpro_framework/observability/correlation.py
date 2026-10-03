"""The execution context on every signal — PRD_03 §4.10.

    release          APP_RELEASE verbatim            log · resource · metric · GlitchTip
    service.name     the artifact                    log · resource · metric · GlitchTip
    service.version  its version                     log · resource · metric · GlitchTip
    tenant           the execution's, else the deployment's   log · span · metric · GlitchTip
    user_id          the execution's, else the identity's      log · span · GlitchTip user
    <any key>        what the context carries        log · span · GlitchTip · metric when declared

Context: the execution context is the framework's language — the entrypoint, the application
(`bus.context`), an interceptor, a hook and the handler (`self.context`) all write to it. So it is
what every signal reads, when that signal is recorded: a key set midway counts on the span, the
metric and the GlitchTip event that close the execution. The only filter is the one the project
chose, `hide_in_logs`; nothing is refused for its content.

Nothing here imports a backend, and nothing here raises.
"""

import sys
from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Generator, Protocol
from urllib.parse import unquote

from sincpro_framework.context.domain.keys import (
    LEGACY_KEYS,
    LEGACY_TENANT,
    LEGACY_USER_ID,
    PLUMBING,
    TENANT_ID,
)
from sincpro_framework.context.infrastructure.tree import current_execution
from sincpro_framework.observability.domain import UNKNOWN, ObservabilityIdentity
from sincpro_framework.sincpro_conf import settings

RELEASE = "release"
SERVICE_NAME = "service.name"
SERVICE_VERSION = "service.version"
TENANT = "tenant"
USER_ID = "user_id"

IDENTITY_KEYS = (SERVICE_NAME, SERVICE_VERSION, RELEASE)
ALWAYS_ON_METRICS = (*IDENTITY_KEYS, TENANT)
"""The keys every series carries, beside what its instrument names and what the buses declare."""

SAID_ABOVE = frozenset({TENANT_ID, *LEGACY_KEYS})
"""Context keys a signal says under its own name — `tenant` and `user_id` — never twice."""


class ExecutionSource(Protocol):
    """Who runs an execution: the `Observability` of its bus."""

    @property
    def identity(self) -> ObservabilityIdentity: ...


_running: ContextVar[ExecutionSource | None] = ContextVar("sincpro_execution", default=None)
_said: ContextVar[dict[str, Any] | None] = ContextVar("sincpro_execution_said", default=None)
"""What the scopes opened and closed inside the execution said — kept, because its span, its
metrics and its error are recorded after those scopes closed."""


@contextmanager
def running(source: ExecutionSource) -> Generator[None, None, None]:
    """The execution of a use case of `source`'s bus, from its span's opening to its closing."""
    token, said = _running.set(source), _said.set({})
    try:
        yield
    finally:
        try:
            _said.reset(said)
            _running.reset(token)
        except ValueError:
            pass


def remember(scope: Mapping[str, Any]) -> None:
    """A `bus.context(...)` scope closing inside an execution: what it said still counts for the
    signals the execution records when it closes. Never raises."""
    said = _said.get()
    if said is None:
        return
    try:
        said.update(scope)
    except Exception:
        pass


def current() -> ExecutionSource | None:
    return _running.get()


# --- where a value comes from ---------------------------------------------------------------------


def resource_attributes() -> dict[str, str]:
    """`OTEL_RESOURCE_ATTRIBUTES` as OpenTelemetry defines it: `key=value` pairs joined by
    commas, each part percent-decoded and stripped."""
    import os

    declared: dict[str, str] = {}
    for pair in os.environ.get("OTEL_RESOURCE_ATTRIBUTES", "").split(","):
        if "=" not in pair:
            continue
        key, value = pair.split("=", 1)
        key, value = unquote(key).strip(), unquote(value).strip()
        if key:
            declared[key] = value
    return declared


def deployment_tenant() -> str:
    """The tenant the deployment serves: `TENANT`, else `tenant` in `OTEL_RESOURCE_ATTRIBUTES`
    (what a host such as Odoo sets); empty when neither says one."""
    try:
        declared = (getattr(settings, "tenant", None) or "").strip()
    except Exception:
        declared = ""
    return declared or resource_attributes().get(TENANT, "")


def _identity() -> Any:
    """The authenticated caller, when auth is in play — `None` otherwise."""
    # Only when auth is already loaded: a service that never authenticates has no identity to
    # read, and must not load the auth layer (and the DDD one under it) for it.
    module = sys.modules.get("sincpro_framework.auth.security_context")
    if module is None:
        return None
    try:
        identity = module.current_identity()
    except Exception:
        return None
    return None if getattr(identity, "is_anonymous", True) else identity


def execution_context() -> Mapping[str, Any]:
    """The context of the execution in progress, live — what a handler, a hook or an interceptor
    wrote to it included, and its identity last: `execution_id`, `causation_id` and
    `correlation_id` are the execution's own, whatever the context was handed. Outside a use case,
    what a caller handed the next bus (`carrying`).
    """
    try:
        from sincpro_framework.context.infrastructure.tree import live_context

        live = live_context()
    except Exception:
        live = {}
    said = _said.get()
    running = current_execution()
    if not said and running is None:
        return live
    return {**live, **(said or {}), **(running.chain() if running is not None else {})}


def _hidden() -> frozenset[str]:
    try:
        from sincpro_framework.context.infrastructure.tree import hidden_keys

        return hidden_keys()
    except Exception:
        return frozenset()


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(getattr(value, "value", value)).strip()


def tenant(context: Mapping[str, Any] | None = None) -> str:
    """The execution's tenant: the context's `tenant_id` (or `tenant`, the key before it); else
    the authenticated identity's; else the deployment's."""
    context = execution_context() if context is None else context
    return (
        _text(context.get(TENANT_ID))
        or _text(context.get(LEGACY_TENANT))
        or _text(getattr(_identity(), "tenant", None))
        or deployment_tenant()
    )


def user_id(context: Mapping[str, Any] | None = None) -> str:
    """Who the execution acts for: the context's `user_id` (or `user.id`, the key before it);
    else the authenticated subject."""
    context = execution_context() if context is None else context
    return (
        _text(context.get(USER_ID))
        or _text(context.get(LEGACY_USER_ID))
        or _text(getattr(_identity(), "subject", None))
    )


def identity_keys(identity: ObservabilityIdentity | None) -> dict[str, str]:
    """`service.name`, `service.version` and `release` — what is known of them."""
    if identity is None:
        return {}
    keys = {
        SERVICE_NAME: identity.service_name,
        SERVICE_VERSION: identity.service_version,
        RELEASE: identity.release if identity.artifact != UNKNOWN else "",
    }
    return {key: value for key, value in keys.items() if value}


def running_identity() -> ObservabilityIdentity | None:
    """Who is emitting now: the bus running the execution, else the process's announcement."""
    source = _running.get()
    if source is not None:
        try:
            return source.identity
        except Exception:
            return None
    try:
        from sincpro_framework.observability.registry import registry

        return registry.process_identity()
    except Exception:
        return None


def execution_keys() -> dict[str, Any]:
    """What an execution says on its span and its GlitchTip event: its tenant, its user, and every
    key of its context — the hidden ones and the trace's own plumbing left out."""
    context = execution_context()
    hidden = _hidden()
    keys: dict[str, Any] = {}
    who = tenant(context)
    if who:
        keys[TENANT] = who
    user = user_id(context)
    if user:
        keys[USER_ID] = user
    for key, value in context.items():
        if not isinstance(key, str) or key in PLUMBING or key in SAID_ABOVE or value is None:
            continue
        keys[key] = value
    return {key: value for key, value in keys.items() if key not in hidden}


# --- the labels every series carries --------------------------------------------------------------

_metric_labels: list[str] = []


def declare_metric_labels(keys: Iterable[Any]) -> tuple[str, ...]:
    """Context keys that go on every series of the process, beside `ALWAYS_ON_METRICS`. Each is
    a key (`"company"`) or a field reference (`of(BillingContext).company`). Declared before the
    bus is built: a backend fixes a series' label keys when it creates the instrument."""
    added: list[str] = []
    for one in keys:
        key = getattr(one, "key", one)
        if isinstance(key, str) and key and key not in _metric_labels:
            _metric_labels.append(key)
            added.append(key)
    return tuple(added)


def metric_label_keys() -> tuple[str, ...]:
    """Every key a series carries beside its own: the four always, then the declared ones — on
    buses (`UseFramework(metric_labels=...)`) and in the settings (`metric_labels`)."""
    try:
        configured = tuple(getattr(settings, "metric_labels", None) or ())
    except Exception:
        configured = ()
    keys: list[str] = list(ALWAYS_ON_METRICS)
    for key in (*_metric_labels, *configured):
        if isinstance(key, str) and key and key not in keys:
            keys.append(key)
    return tuple(keys)


def metric_labels(keys: tuple[str, ...]) -> dict[str, str]:
    """The values of `keys` for a measurement taken now; a key with no value is left off."""
    identity = identity_keys(running_identity())
    context = execution_context()
    hidden = _hidden()
    labels: dict[str, str] = {}
    for key in keys:
        if key in hidden:
            continue
        if key in identity:
            value = identity[key]
        elif key == TENANT:
            value = tenant(context)
        elif key == USER_ID:
            value = user_id(context)
        else:
            value = _text(context.get(key))
        if value:
            labels[key] = value
    return labels


def reset_metric_labels() -> None:
    """Forget the labels buses declared — what a test starts from."""
    _metric_labels.clear()


__all__ = [
    "ALWAYS_ON_METRICS",
    "IDENTITY_KEYS",
    "RELEASE",
    "SERVICE_NAME",
    "SERVICE_VERSION",
    "TENANT",
    "USER_ID",
    "declare_metric_labels",
    "deployment_tenant",
    "execution_context",
    "execution_keys",
    "identity_keys",
    "metric_label_keys",
    "metric_labels",
    "reset_metric_labels",
    "running",
    "tenant",
    "user_id",
]
