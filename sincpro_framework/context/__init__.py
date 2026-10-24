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
                     Draft, DraftConflict, ContextCodec
    infrastructure/  the node in play, executions, what scopes say, the levels shared through
                     the store (process, global, a node per execution)
    adapters/        the codecs a context is written with
    entrypoint/      Context, use_context() and carrying(), Drafts — whoever the context says —
                     requires_context and the providers,
                     the threads and pools, what a bus holds (bus.context, self.context)

Where the context is kept is the store's (`sincpro_framework.store`): ContextStore, Entry,
EntryConflict, InMemoryContexts, KeyValueContexts and store_context_in are re-exported here, so
`from sincpro_framework.context import KeyValueContexts` keeps working.

Every name below is loaded when first asked for: the bus's own modules import this package, and it
never imports a bus at import time.
"""

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sincpro_framework.context.adapters.codecs import PickleCodec, PlainCodec, TypedCodec
    from sincpro_framework.context.domain.codec import ContextCodec
    from sincpro_framework.context.domain.drafts import Draft
    from sincpro_framework.context.domain.exceptions import DraftConflict, EntryConflict
    from sincpro_framework.context.domain.execution import Execution, chained
    from sincpro_framework.context.domain.keys import ContextKey, travelling
    from sincpro_framework.context.domain.level import EntrypointKind, Level, Origin
    from sincpro_framework.context.entrypoint.drafts import Drafts
    from sincpro_framework.context.entrypoint.facade import Context, carrying, use_context
    from sincpro_framework.context.entrypoint.providers import requires_context
    from sincpro_framework.context.entrypoint.threads import (
        ContextExecutor,
        ContextThread,
        in_context,
    )
    from sincpro_framework.store.adapters.stores import InMemoryContexts, KeyValueContexts
    from sincpro_framework.store.domain.store import ContextStore, Entry
    from sincpro_framework.store.entrypoint.resolution import store_context_in

_WHERE = {
    "PickleCodec": "adapters.codecs",
    "PlainCodec": "adapters.codecs",
    "TypedCodec": "adapters.codecs",
    "InMemoryContexts": "sincpro_framework.store.adapters.stores",
    "KeyValueContexts": "sincpro_framework.store.adapters.stores",
    "Draft": "domain.drafts",
    "DraftConflict": "domain.exceptions",
    "Drafts": "entrypoint.drafts",
    "ContextCodec": "domain.codec",
    "Execution": "domain.execution",
    "chained": "domain.execution",
    "ContextKey": "domain.keys",
    "travelling": "domain.keys",
    "EntrypointKind": "domain.level",
    "Level": "domain.level",
    "Origin": "domain.level",
    "ContextStore": "sincpro_framework.store.domain.store",
    "Entry": "sincpro_framework.store.domain.store",
    "EntryConflict": "domain.exceptions",
    "Context": "entrypoint.facade",
    "use_context": "entrypoint.facade",
    "requires_context": "entrypoint.providers",
    "ContextExecutor": "entrypoint.threads",
    "ContextThread": "entrypoint.threads",
    "in_context": "entrypoint.threads",
    "carrying": "entrypoint.facade",
    "store_context_in": "sincpro_framework.store.entrypoint.resolution",
}

__all__ = [
    "Context",
    "ContextCodec",
    "ContextExecutor",
    "ContextKey",
    "ContextStore",
    "ContextThread",
    "Draft",
    "DraftConflict",
    "Drafts",
    "Entry",
    "EntryConflict",
    "EntrypointKind",
    "Execution",
    "InMemoryContexts",
    "KeyValueContexts",
    "Level",
    "Origin",
    "PickleCodec",
    "PlainCodec",
    "TypedCodec",
    "carrying",
    "chained",
    "in_context",
    "store_context_in",
    "requires_context",
    "travelling",
    "use_context",
]


def __getattr__(name: str) -> Any:
    """A public name loaded the first time it is asked for, then kept on the module so the
    next read is a plain attribute.

        from sincpro_framework.context import use_context   imports entrypoint.facade now
        sincpro_framework.context.Nope                      AttributeError
    """
    where = _WHERE.get(name)
    if where is None:
        raise AttributeError(f"module 'sincpro_framework.context' has no attribute {name!r}")
    module = (
        where
        if where.startswith("sincpro_framework.")
        else f"sincpro_framework.context.{where}"
    )
    value = getattr(import_module(module), name)
    globals()[name] = value
    return value
