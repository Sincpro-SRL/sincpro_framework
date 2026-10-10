"""`BusContextMixin`: what a `UseFramework` mixes in to hold the context — its published node, its
schema, its providers, the store it shares, and `bus.context(...)` with the registrations that
configure them."""

from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager
from datetime import timedelta
from typing import TYPE_CHECKING, Any, cast

from pydantic import TypeAdapter

from sincpro_framework.common.ids import new_entity_id
from sincpro_framework.context.domain.execution import CONTEXT_NODE
from sincpro_framework.context.domain.level import SCOPES, EntrypointKind, Level
from sincpro_framework.context.domain.node import ContextNode, Values
from sincpro_framework.context.domain.store import ContextStore
from sincpro_framework.context.entrypoint.bus import FrameworkContext
from sincpro_framework.context.entrypoint.facade import Context
from sincpro_framework.context.entrypoint.providers import (
    ContextProvider,
    ContextProviderFunction,
)
from sincpro_framework.context.infrastructure.distributed import SharedContext, joined
from sincpro_framework.context.infrastructure.tree import ROOT, current, entered

if TYPE_CHECKING:
    from sincpro_framework.bus import FrameworkBus
    from sincpro_framework.observability import Observability


class BusContextMixin:
    """What a `UseFramework` adds to hold the context: its published node, its schema, its
    providers."""

    bus: "FrameworkBus | None"
    observability: "Observability"
    _registrations: list[Callable[[Any], Any]]

    def _init_context_storage(self) -> None:
        self._published = Values()
        """What this bus publishes for every execution of its own — its `Level.BUS` node's values."""
        self._context_schema: TypeAdapter[Any] | None = None
        self._context_providers: list[ContextProvider] = []
        self._shared_context: SharedContext | None = None
        """The store this bus shares its context through — `bus.context_store(store)`."""

    @property
    def _context_label(self) -> str:
        return getattr(self, "_logger_name", "")

    def _entered_here(self) -> bool:
        """Whether the node in play stands in a scope of this bus already."""
        return any(node.owner is self and node.level in SCOPES for node in current().chain())

    def _bus_node(self, parent: ContextNode) -> ContextNode:
        return ContextNode(
            Level.BUS, parent, self._published, self._context_label, owner=self
        )

    @contextmanager
    def _scope(
        self, values: Mapping[Any, Any], kind: EntrypointKind | None = None
    ) -> Generator[ContextNode, None, None]:
        """A scope of this bus under the node in play — under its own node the first time.

        1. The bus's node above it, the first time a call reaches this bus.
        2. An entrance of a bus that shares its context opens under the chain the sender handed
           on (`sincpro.context_node`), read back from the store.
        3. Final: the node, kept in the store when this bus shares its context.
        """
        parent = current()
        if not self._entered_here():
            parent = self._bus_node(parent)
        level = Level.SCOPE if parent.nearest(Level.ENTRYPOINT) else Level.ENTRYPOINT
        handed = None
        if CONTEXT_NODE in values:
            handed = values[CONTEXT_NODE]
            values = {key: value for key, value in values.items() if key != CONTEXT_NODE}
        sharing = self._shared_context
        if level is Level.ENTRYPOINT:
            kind = kind or EntrypointKind.DIRECT
            if sharing is not None:
                parent, values = sharing.entrance(values, handed, parent)
        node = ContextNode(level, parent, values, self._context_label, owner=self, kind=kind)
        if sharing is not None:
            sharing.kept(node, new_entity_id())
        with entered(node):
            yield node

    def _get_context(self) -> Context:
        """The context as this bus sees it from where it is read."""
        node = current()
        if not self._entered_here():
            node = self._bus_node(node)
        return Context(node, owner=self)

    def current_context(self) -> Context:
        """The context in play, as this bus sees it — the object `self.context` is in a handler,
        read-only, from anywhere:

            Database(url, actor=lambda: bus.current_context().get("user_id"))

        A callable stored once at wiring time answers for every later request: it is read when
        it is asked. Writing is `bus.context(...)`'s, `self.context`'s and `use_context()`'s.
        """
        context = self._get_context()
        return Context(context._node, self, writable=False)

    def _inherited_context(self) -> dict[str, Any]:
        """What the execution in progress — on this bus or the one that called it — sees."""
        return {
            key: value for key, value in current().flattened().items() if isinstance(key, str)
        }

    def _set_context(self, context: Mapping[Any, Any]) -> None:
        self._get_context().replace(context)

    def _clean_context(self) -> None:
        self._set_context({})

    def _coerced(self, values: Mapping[Any, Any]) -> dict[Any, Any]:
        """`values` with the keys the bus's schema names validated and typed; the rest as given."""
        if self._context_schema is None:
            return dict(values)
        named = {key: value for key, value in values.items() if isinstance(key, str)}
        typed = self._context_schema.validate_python(named)
        return {**values, **(typed if isinstance(typed, Mapping) else typed.__dict__)}

    def _bind_context_to_handlers(self) -> None:
        if self.bus is None:
            return
        for feature in self.bus.feature_bus.feature_registry.values():
            feature.bind_to_framework(self)
        for app_service in self.bus.app_service_bus.app_service_registry.values():
            app_service.bind_to_framework(self)

    def context(
        self,
        context_to_set: Mapping[Any, Any] | None = None,
        global_scope: bool = False,
        kind: EntrypointKind | None = None,
        restore: str | list[str] | None = None,
        keep_as: str | None = None,
        ttl: timedelta | None = None,
        store: ContextStore | None = None,
    ) -> FrameworkContext:
        """A scope of this bus for the block — every execution inside reads it, nothing outside does.

            with app.context({"correlation_id": "123", "user_id": "456"}) as app_with_context:
                app_with_context(some_dto)

            with app.context({"tenant_id": "acme"}, global_scope=True):    every execution of
                ...                                                          this bus reads it
            with app.context(restore=["tenant:acme", f"session:{sid}"]):    what a store kept
            with app.context(values, keep_as="sale-77", ttl=timedelta(hours=1)):

        Context: isolated per thread and task. The first scope of a flow is its entrypoint —
        `kind` says which entrance opened it, `EntrypointKind.DIRECT` when none is named; a scope
        inside another is a child of it. `restore` reads each key from the store (`store`, else
        the nearest `ContextStore` in the context), in order, under `context_to_set`; `keep_as`
        keeps what the scope sees, with the flow's chain, for whoever resumes it.
        """
        return FrameworkContext(
            cast(Any, self), context_to_set, global_scope, kind, restore, keep_as, ttl, store
        )

    def context_provider(
        self, needs: Sequence[str] = (), gives: Sequence[str] = ()
    ) -> Callable[[ContextProviderFunction], ContextProviderFunction]:
        """Register the decorated function as what gives `gives` from `needs` — run when an
        execution of this bus opens, has every key it needs and lacks one it gives.

            @siat_soap_sdk.context_provider(needs=["nit_id"], gives=["TOKEN", "SIAT_ENV"])
            def siat_credentials(context): ...

        Its answer is written on that execution's node: what it runs finds it there.
        """

        def register(function: ContextProviderFunction) -> ContextProviderFunction:
            self._context_providers.append(
                ContextProvider(function, tuple(needs), tuple(gives))
            )
            self._registrations.append(
                lambda bus: bus.context_provider(needs, gives)(function)
            )
            return function

        return register

    def context_schema(self, schema: type) -> None:
        """Validate and type what a scope of this bus is opened with against `schema` — the
        `TypedDict` or DTO a project declares its context with:

            siat_soap_sdk.context_schema(SIATContext)     SIAT_ENV=2 arrives a SIATEnvironment

        The keys the schema names are validated; any other key passes as it was given."""
        self._context_schema = TypeAdapter(schema)
        self._registrations.append(lambda bus: bus.context_schema(schema))

    def context_store(
        self,
        store: ContextStore,
        ttl: timedelta = timedelta(hours=24),
        every: timedelta = timedelta(seconds=1),
    ) -> None:
        """Share this bus's context through `store` — and keep and restore with it.

            billing.context_store(KeyValueContexts(RedisKeyValue(redis)))

        The API does not change: `self.context`, `use_context()`, `bus.context(...)` read and
        write as always, and every node of this bus's executions is kept in the store for `ttl`.
        A bus of another service — another project too — that set the same store reads the chain
        it was handed on, the process is kept per service, and `Level.GLOBAL` is shared by every
        one of them; what others wrote is read again at most once per `every`. Nothing is shared
        by a bus that did not say so. See `docs/prd/PRD_24_shared-context.md`.
        """
        self._published.set(ContextStore, store)
        self._shared_context = SharedContext(store, ttl, every)
        joined(ROOT, self._shared_context, self.observability.identity.service_name)
        self._registrations.append(lambda bus: bus.context_store(store, ttl=ttl, every=every))
