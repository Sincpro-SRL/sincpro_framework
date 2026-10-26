"""The context — one component, read from anywhere, scoped, typed, carried, and saved when asked
(PRD_22).

    from sincpro_framework.context import use_context, ContextLevel

    context = use_context()                 a Feature, an interceptor, a hook, an adapter
    context["tenant_id"]                    the nearest node wins
    context.entrypoint.kind                 EntrypointKind.CRON
    with context.scoped({"tenant_id": "beta"}):   a child scope for a block

    with bus.context(SIATContext(TOKEN="…", SIAT_ENV=…)):   the syntax the buses always had

**A tree of nodes, never one context overwritten**: ROOT (the process) → BUS (what a bounded context
publishes) → ENTRYPOINT (what the entrance knew) → APPLICATION / FEATURE (each execution) → HOOK →
SCOPE (a block). `contextvars` says which node is current per thread and task; the nodes are
copy-on-write, so threads never tear them.

    domain/          ContextLevel, EntrypointKind, Origin, ContextNode and Values, Execution and
                     Chain, the standard keys, Owner, ContextProvider, Draft, DraftConflict,
                     ContextCodec, ContextStore and Entry, EntryConflict
    infrastructure/  the node in play, what closed scopes said, the levels shared through the
                     store (process, global, a node per execution)
    adapters/        the codecs a context is written with, the headers it travels in;
                     InMemoryContexts, KeyValueContexts and their entries
    services/        executions opened and handed on, nodes opened and saved, what travels, the
                     providers prepared, Drafts
    entrypoint/      Context, use_context() and carrying(), requires_context, the threads and
                     pools, what a bus holds (bus.context, bus.store()), where the context is
                     saved (store_context_in), and the facades the rest of the framework calls:
                     context_executions, context_scopes, context_propagation

Where the context is saved is the context's to say, on any `KeyValueStore` of
`sincpro_framework.data_layer.store` — the store itself knows nothing of the context. The context
stores and `store_context_in` are exported from here only.

Another package of the framework takes from here the facades of `entrypoint/` —
`context_executions`, `context_scopes`, `context_propagation` — and the types it names
(`ContextNode`, `ContextProvider`, …); the facades stay in `entrypoint/`, this door only
gathers them. The
context imports no bus: the handlers' `self.context` asks the bus they are bound to
(`ContextConsumer`, beside `Feature`), so this door loads everything at once.
"""

from sincpro_framework.context.adapters.codecs import PickleCodec, PlainCodec, TypedCodec
from sincpro_framework.context.adapters.stores import InMemoryContexts, KeyValueContexts
from sincpro_framework.context.domain.codec import ContextCodec
from sincpro_framework.context.domain.drafts import Draft
from sincpro_framework.context.domain.exceptions import DraftConflict, EntryConflict
from sincpro_framework.context.domain.execution import Chain, Execution
from sincpro_framework.context.domain.keys import ContextKey
from sincpro_framework.context.domain.level import ContextLevel, EntrypointKind, Origin
from sincpro_framework.context.domain.node import ContextNode
from sincpro_framework.context.domain.owner import Owner
from sincpro_framework.context.domain.providers import ContextProvider
from sincpro_framework.context.domain.store import ContextStore, Entry
from sincpro_framework.context.entrypoint.bus import FrameworkContext
from sincpro_framework.context.entrypoint.executions import context_executions
from sincpro_framework.context.entrypoint.facade import Context, carrying, use_context
from sincpro_framework.context.entrypoint.propagation import context_propagation
from sincpro_framework.context.entrypoint.providers import requires_context
from sincpro_framework.context.entrypoint.resolution import store_context_in
from sincpro_framework.context.entrypoint.scopes import context_scopes
from sincpro_framework.context.entrypoint.threads import (
    ContextExecutor,
    ContextThread,
    in_context,
)
from sincpro_framework.context.infrastructure.travel import travelling
from sincpro_framework.context.services.drafts import Drafts

__all__ = [
    "Chain",
    "Context",
    "ContextCodec",
    "ContextExecutor",
    "ContextKey",
    "ContextNode",
    "ContextProvider",
    "ContextStore",
    "ContextThread",
    "Draft",
    "DraftConflict",
    "Drafts",
    "Entry",
    "EntryConflict",
    "EntrypointKind",
    "FrameworkContext",
    "Execution",
    "InMemoryContexts",
    "KeyValueContexts",
    "ContextLevel",
    "Origin",
    "Owner",
    "PickleCodec",
    "PlainCodec",
    "TypedCodec",
    "carrying",
    "context_executions",
    "in_context",
    "context_propagation",
    "store_context_in",
    "requires_context",
    "context_scopes",
    "travelling",
    "use_context",
]
