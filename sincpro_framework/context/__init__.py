"""The context — one component, read from anywhere, scoped, typed, carried, and kept when asked
(PRD_22).

    from sincpro_framework.context import use_context, Level

    context = use_context()                 a Feature, an interceptor, a hook, an adapter
    context["tenant_id"]                    the nearest node wins
    context.entrypoint.kind                 EntrypointKind.CRON
    with context.scoped({"tenant_id": "beta"}):   a child scope for a block

    with bus.context(SIATContext(TOKEN="…", SIAT_ENV=…)):   the syntax the buses always had

**A tree of nodes, never one context overwritten**: ROOT (the process) → BUS (what a bounded context
publishes) → ENTRYPOINT (what the entrance knew) → APPLICATION / FEATURE (each execution) → HOOK →
SCOPE (a block). `contextvars` says which node is current per thread and task; the nodes are
copy-on-write, so threads never tear them.

    domain/          Level, EntrypointKind, Origin, ContextNode, Execution, the standard keys,
                     the ports — ContextStore, ContextCodec
    infrastructure/  the node in play, executions, what scopes say, the shared store
    adapters/        InMemoryContexts, KeyValueContexts (Redis, Valkey, Memcached), the codecs
    entrypoint/      Context, use_context() and carrying(), requires_context and the providers,
                     the threads and pools, what a bus holds (bus.context, self.context)

Every name below is loaded when first asked for: the bus's own modules import this package, and it
never imports a bus at import time.
"""

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sincpro_framework.context.adapters.codecs import PickleCodec, PlainCodec, TypedCodec
    from sincpro_framework.context.adapters.stores import InMemoryContexts, KeyValueContexts
    from sincpro_framework.context.domain.codec import ContextCodec
    from sincpro_framework.context.domain.execution import (
        CAUSATION_ID,
        CORRELATION_ID,
        EXECUTION_ID,
        Execution,
        chained,
    )
    from sincpro_framework.context.domain.keys import (
        TENANT_ID,
        USER_ID,
        travelling,
    )
    from sincpro_framework.context.domain.level import EntrypointKind, Level, Origin
    from sincpro_framework.context.domain.store import ContextStore
    from sincpro_framework.context.entrypoint.facade import Context, carrying, use_context
    from sincpro_framework.context.entrypoint.providers import requires_context
    from sincpro_framework.context.entrypoint.threads import (
        ContextExecutor,
        ContextThread,
        in_context,
    )

_WHERE = {
    "PickleCodec": "adapters.codecs",
    "PlainCodec": "adapters.codecs",
    "TypedCodec": "adapters.codecs",
    "InMemoryContexts": "adapters.stores",
    "KeyValueContexts": "adapters.stores",
    "ContextCodec": "domain.codec",
    "CAUSATION_ID": "domain.execution",
    "CORRELATION_ID": "domain.execution",
    "EXECUTION_ID": "domain.execution",
    "Execution": "domain.execution",
    "chained": "domain.execution",
    "TENANT_ID": "domain.keys",
    "USER_ID": "domain.keys",
    "travelling": "domain.keys",
    "EntrypointKind": "domain.level",
    "Level": "domain.level",
    "Origin": "domain.level",
    "ContextStore": "domain.store",
    "Context": "entrypoint.facade",
    "use_context": "entrypoint.facade",
    "requires_context": "entrypoint.providers",
    "ContextExecutor": "entrypoint.threads",
    "ContextThread": "entrypoint.threads",
    "in_context": "entrypoint.threads",
    "carrying": "entrypoint.facade",
}

__all__ = [
    "CAUSATION_ID",
    "CORRELATION_ID",
    "Context",
    "ContextCodec",
    "ContextExecutor",
    "ContextStore",
    "ContextThread",
    "EXECUTION_ID",
    "EntrypointKind",
    "Execution",
    "InMemoryContexts",
    "KeyValueContexts",
    "Level",
    "Origin",
    "PickleCodec",
    "PlainCodec",
    "TENANT_ID",
    "TypedCodec",
    "USER_ID",
    "carrying",
    "chained",
    "in_context",
    "requires_context",
    "travelling",
    "use_context",
]


def __getattr__(name: str) -> Any:
    where = _WHERE.get(name)
    if where is None:
        raise AttributeError(f"module 'sincpro_framework.context' has no attribute {name!r}")
    value = getattr(import_module(f"sincpro_framework.context.{where}"), name)
    globals()[name] = value
    return value
