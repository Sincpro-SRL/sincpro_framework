"""The context as a bus holds it: `bus.context(...)`, `self.context`, `bus.current_context()`.

    with bus.context(SIATContext(TOKEN="…", SIAT_ENV=SIATEnvironment.TEST)):   a scope, as always
        bus(CommandSendDocument(...))
    with bus.context({"tenant_id": "acme"}, global_scope=True):   what every execution of this bus reads
    with bus.context(restore=["tenant:acme", f"session:{sid}"]):  scopes a store kept
    with bus.context(values, keep_as="sale-77", ttl=timedelta(hours=1)):   kept for whoever resumes

**Entering a bus is entering its node.** The first time a call reaches a bus, the bus's own node
(`Level.BUS` — what it publishes for every execution of its own) goes under the node in play, and the
call's scope under it: an entrypoint when nothing opened the flow, a scope otherwise. A nested call to
the same bus, or one inside its `bus.context(...)`, joins the scope already open.

**What `global_scope=True` writes is the bus's node**: every execution of this bus reads it live —
running ones included — and the block's end puts back what was there.
"""

from collections.abc import Generator, Mapping
from contextlib import contextmanager
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Literal

from pydantic import TypeAdapter

from sincpro_framework.context.domain.execution import CONTEXT_NODE
from sincpro_framework.context.domain.keys import standardized
from sincpro_framework.context.domain.level import SCOPES, EntrypointKind, Level
from sincpro_framework.context.domain.node import ContextNode, Values
from sincpro_framework.context.domain.store import ContextStore
from sincpro_framework.context.entrypoint.facade import Context
from sincpro_framework.context.infrastructure.distributed import SharedContext
from sincpro_framework.context.infrastructure.providers import ContextProvider
from sincpro_framework.context.infrastructure.tree import current, entered
from sincpro_framework.ids import new_entity_id
from sincpro_framework.observability.correlation import remember

if TYPE_CHECKING:
    from sincpro_framework.bus import FrameworkBus
    from sincpro_framework.use_bus import UseFramework


class ContextMixin:
    """What a `UseFramework` adds to hold the context: its published node, its schema, its
    providers."""

    bus: "FrameworkBus | None"

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


class FrameworkContext:
    """`bus.context(...)`: a scope of the bus for the block — or, with `global_scope`, what the
    bus publishes."""

    def __init__(
        self,
        framework_instance: "UseFramework",
        context: Mapping[Any, Any] | None = None,
        global_scope: bool = False,
        kind: EntrypointKind | None = None,
        restore: "str | list[str] | None" = None,
        keep_as: str | None = None,
        ttl: timedelta | None = None,
        store: ContextStore | None = None,
    ) -> None:
        self._is_entered: bool = False
        self.framework = framework_instance
        self.context: dict[Any, Any] = standardized(context or {})
        self.global_scope = global_scope
        self.kind = kind
        self.restore = restore
        self.keep_as = keep_as
        self.ttl = ttl
        self.store = store
        self._node: ContextNode | None = None
        self._block: Any = None
        self._published_before: Mapping[Any, Any] | None = None

    def __enter__(self) -> "UseFramework":
        if self._is_entered:
            raise RuntimeError("Context manager is already entered")
        self._is_entered = True
        framework = self.framework
        if self.global_scope:
            self._published_before = framework._published.current
            framework._published.update(framework._coerced(self.context))
        else:
            values = self.context
            if self.restore:
                restored = framework._get_context().restored(self.restore, self.store)
                values = {**restored, **values}
            self._block = framework._scope(framework._coerced(values), self.kind)
            node = self._node = self._block.__enter__()
            if self.keep_as:
                Context(node, framework).keep(self.keep_as, self.ttl, self.store)
        framework.logger.debug(f"with context: {sorted(map(str, self.context))}")
        return framework

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> Literal[False]:
        if self.global_scope:
            if self._published_before is not None:
                self.framework._published.replace(self._published_before)
        elif self._block is not None and self._node is not None:
            # What the scope said still counts for the execution it was opened in: its span,
            # its metrics and its error close after the scope does (PRD_03 §4.10).
            remember(self._node.own)
            self._block.__exit__(exc_type, exc_val, exc_tb)
        return False
