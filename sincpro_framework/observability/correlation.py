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

    with running(observability):                   # the bus's span is open
        tenant()             ->  "acme"            # context, else identity, else deployment
        execution_keys()     ->  {"tenant": "acme", "user_id": "u7", "company": "3"}
        metric_labels(metric_label_keys())
                             ->  {"service.name": "billing", "tenant": "acme", ...}
"""

import sys
from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Generator, Protocol

from sincpro_framework.context import (
    ContextKey,
    context_executions,
    context_propagation,
    context_scopes,
)
from sincpro_framework.observability.domain import (
    TENANT,
    UNKNOWN,
    ObservabilityIdentity,
    deployment_tenant,
)
from sincpro_framework.observability.registry import observability_registry
from sincpro_framework.sincpro_conf import settings

RELEASE = "release"
SERVICE_NAME = "service.name"
SERVICE_VERSION = "service.version"
USER_ID = "user_id"

IDENTITY_KEYS = (SERVICE_NAME, SERVICE_VERSION, RELEASE)
ALWAYS_ON_METRICS = (*IDENTITY_KEYS, TENANT)
"""The keys every series carries, beside what its instrument names and what the buses declare."""

SAID_ABOVE = frozenset({ContextKey.TENANT_ID, TENANT})
"""Context keys a signal says under its own name — `tenant` and `user_id` — never twice."""


class ExecutionSource(Protocol):
    """Who runs an execution: the `Observability` of its bus."""

    @property
    def identity(self) -> ObservabilityIdentity:
        """Who the bus is — its service, version and release."""
        ...


_running: ContextVar[ExecutionSource | None] = ContextVar("sincpro_execution", default=None)


@contextmanager
def running(source: ExecutionSource) -> Generator[None, None, None]:
    """The execution of a use case of `source`'s bus, from its span's opening to its closing.

        with running(observability):
            current()              ->  observability
            running_identity()     ->  observability.identity
        current()                  ->  None — or the source running before it, put back

    What the scopes opened inside the block say is collected (`context_scopes.collecting()`), so the
    signals that close the execution still read it.
    """
    token = _running.set(source)
    try:
        with context_scopes.collecting():
            yield
    finally:
        try:
            _running.reset(token)
        except ValueError:
            pass


def current() -> ExecutionSource | None:
    """The `Observability` of the bus running the execution in play — `None` outside one."""
    return _running.get()


# --- where a value comes from ---------------------------------------------------------------------


def _identity() -> Any:
    """The authenticated caller, when auth is in play — `None` otherwise."""
    # Only when auth is already loaded: a service that never authenticates has no identity to
    # read, and must not load the auth layer (and the DDD one under it) for it.
    module = sys.modules.get("sincpro_framework.auth")
    if module is None:
        return None
    try:
        identity = module.security.identity
    except Exception:
        return None
    return None if getattr(identity, "is_anonymous", True) else identity


def execution_context() -> Mapping[str, Any]:
    """The context of the execution in progress, live — what a handler, a hook or an interceptor
    wrote to it included, and its identity last: `execution_id`, `causation_id` and
    `correlation_id` are the execution's own, whatever the context was handed. Outside a use case,
    what a caller handed the next bus (`carrying`).

        execution_context()   ->  {"tenant_id": "acme", "company": 3,
                                   "execution_id": "…", "causation_id": "…",
                                   "correlation_id": "…"}

    Layered as live context, then what closed scopes said, then the execution's chain; the
    live context alone when there is neither — an empty one when the tree cannot be read.
    """
    try:
        live = context_scopes.live()
    except Exception:
        live = {}
    closed = context_scopes.closed()
    running = context_executions.current()
    if not closed and running is None:
        return live
    return {**live, **closed, **(running.chain() if running is not None else {})}


def _hidden() -> frozenset[str]:
    """The context keys the project hid from the signals (`hide_in_logs`) — none when the
    context tree cannot be read."""
    try:
        return context_scopes.hidden_keys()
    except Exception:
        return frozenset()


def _text(value: Any) -> str:
    """A context value as a label: an enum by its `value`, stripped; `None` as empty.

    _text(" acme ")  ->  "acme"        _text(None)  ->  ""
    """
    if value is None:
        return ""
    return str(getattr(value, "value", value)).strip()


def tenant(context: Mapping[str, Any] | None = None) -> str:
    """The execution's tenant: the context's `tenant_id`; else the authenticated identity's;
    else the deployment's."""
    context = execution_context() if context is None else context
    return (
        _text(context.get(ContextKey.TENANT_ID))
        or _text(getattr(_identity(), "tenant", None))
        or deployment_tenant()
    )


def user_id(context: Mapping[str, Any] | None = None) -> str:
    """Who the execution acts for: the context's `user_id`; else the authenticated
    subject."""
    context = execution_context() if context is None else context
    return _text(context.get(ContextKey.USER_ID)) or _text(
        getattr(_identity(), "subject", None)
    )


def log_fields(identity: ObservabilityIdentity) -> dict[str, str]:
    """Who a log line comes from and for whom, on the line itself: the same release, service,
    version and tenant the spans, the metrics and GlitchTip carry — readable without the
    collector's labels. The tenant and the user are the execution's (PRD_03 §4.10).

        log_fields(ObservabilityIdentity(artifact="sincpro-odoo:18.5.0"))
            ->  {"service_name": "sincpro-odoo", "service_version": "18.5.0",
                 "release": "sincpro-odoo:18.5.0", "tenant": "acme", "user_id": "u7"}
        log_fields(ObservabilityIdentity(bus="billing"))   ->  {"service_name": "billing"}

    1. `service_name` always — the bus stands in when no artifact resolved.
    2. `service_version` when a version is known.
    3. `release` when an artifact resolved.
    4. `tenant`: the execution's (`tenant`), when there is one.
    5. `user_id`: who the execution acts for, when known.
    Final: the fields, never a blank one.
    """
    fields = {"service_name": identity.service_name}
    if identity.service_version:
        fields["service_version"] = identity.service_version
    if identity.artifact != UNKNOWN and identity.release:
        fields["release"] = identity.release
    who = tenant()
    if who:
        fields["tenant"] = who
    user = user_id()
    if user:
        fields["user_id"] = user
    return fields


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
    """Who is emitting now: the bus running the execution, else the process's announcement.

        with running(observability):
            running_identity()   ->  observability.identity        # the bus's
        running_identity()       ->  the process identity, or None when none was announced

    `None` too when the bus's identity cannot be resolved — never raises.
    """
    source = _running.get()
    if source is not None:
        try:
            return source.identity
        except Exception:
            return None
    try:
        return observability_registry.process_identity()
    except Exception:
        return None


def execution_keys() -> dict[str, Any]:
    """What an execution says on its span and its GlitchTip event: its tenant, its user, and every
    key of its context — the hidden ones and the trace's own plumbing left out.

        context  {"tenant_id": "acme", "user_id": "u7", "company": 3, "trace_id": "4bf9…",
                  "note": None}
        execution_keys()   ->  {"tenant": "acme", "user_id": "u7", "company": 3,
                                "execution_id": "…", "causation_id": "…",
                                "correlation_id": "…"}

    1. The execution's context (`execution_context`) and the keys the project hid.
    2. `tenant`: the execution's tenant (`tenant`), when there is one.
    3. `user_id`: who it acts for (`user_id`), when known.
    4. Every other key of the context, its value as it is:
       4.1 a key that is not text, the trace's plumbing (`trace_id`, `span_id`, `carrier`),
           `tenant_id`/`tenant` — already said in 2 — or a `None` value is skipped.
    Final: the keys the project hid (`hide_in_logs`) are dropped.
    """
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
        if (
            not isinstance(key, str)
            or key in context_propagation.trace_keys
            or key in SAID_ABOVE
            or value is None
        ):
            continue
        keys[key] = value
    return {key: value for key, value in keys.items() if key not in hidden}


# --- the labels every series carries --------------------------------------------------------------

_metric_labels: list[str] = []


def declare_metric_labels(keys: Iterable[Any]) -> tuple[str, ...]:
    """Context keys that go on every series of the process, beside `ALWAYS_ON_METRICS`. Each is
    a key (`"company"`) or a field reference (`of(BillingContext).company`). Declared before the
    bus is built: a backend fixes a series' label keys when it creates the instrument.

        declare_metric_labels(["company", of(BillingContext).company, "", "region"])
            ->  ("company", "region")           # each key once; "" is not a key
        declare_metric_labels(["company"])   ->  ()   # already declared

    It answers what this call added; the declared keys stay for the whole process.
    """
    added: list[str] = []
    for one in keys:
        key = getattr(one, "key", one)
        if isinstance(key, str) and key and key not in _metric_labels:
            _metric_labels.append(key)
            added.append(key)
    return tuple(added)


def metric_label_keys() -> tuple[str, ...]:
    """Every key a series carries beside its own: the four always, then the declared ones — on
    buses (`UseFramework(metric_labels=...)`) and in the settings (`metric_labels`).

        declare_metric_labels(["company"])           # settings: metric_labels: [region]
        metric_label_keys()
            ->  ("service.name", "service.version", "release", "tenant", "company", "region")

    In that order, each key once — a declared key that is already there is not repeated.
    """
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
    """The values of `keys` for a measurement taken now; a key with no value is left off.

        metric_labels(("service.name", "release", "tenant", "company", "region"))
            ->  {"service.name": "billing-sdk", "release": "billing-sdk:1.2.0",
                 "tenant": "acme", "company": "3"}        # no region in the context

    1. Who is emitting (`running_identity`) as its identity keys, the execution's context and
       the keys the project hid.
    2. For each key, in order:
       2.1 a hidden key is skipped;
       2.2 `service.name`, `service.version`, `release`: the identity's;
       2.3 `tenant` and `user_id`: the execution's (`tenant`, `user_id`);
       2.4 any other: the context's value as text (`_text`).
    Final: the keys with a non-empty value.
    """
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
