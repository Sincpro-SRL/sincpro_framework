"""`Context` — the one object the context is, wherever it is reached; `use_context()` reaches it.

    context = use_context()                  in a Feature, an interceptor, a hook, an adapter
    context["tenant_id"]                     the effective value: the nearest node wins
    context.get("lang", "es")
    context[SiatSettings]                    a value kept under its type, settings among them

    context["note"] = "x"                    the scope of the call — as `self.context` always wrote
    context.set("pos_id", 3)                 this node only: what it runs afterwards sees it
    with context.scoped({"tenant_id": "beta"}):     a child scope for a block

    context.level · context.parent · context.root · context.entrypoint · context.application
    context.feature · context.hook · context.bus · context.at(Level.ENTRYPOINT)
    context.own · context.origin("tenant_id") · context.lineage() · context.execution

**Two ways to write, on purpose.** As a mapping — `context[key] = value`, `update`, `del` — a write
lands on the scope of the call: the `bus.context(...)` block, or the call itself; every execution of
that call sees it, the way `self.context` always behaved. `set` writes the node in play only — a
Feature's own value, reaching what it runs and never its siblings or its caller.

`self.context` in a handler and `bus.current_context()` are this same object.
"""

from collections.abc import Generator, Mapping
from contextlib import contextmanager
from datetime import timedelta
from typing import Any, overload

from sincpro_framework.context.domain.execution import (
    CAUSATION_ID,
    CORRELATION_ID,
    EXECUTION_ID,
    Execution,
)
from sincpro_framework.context.domain.keys import standardized, travelling
from sincpro_framework.context.domain.level import EntrypointKind, Level, Origin
from sincpro_framework.context.domain.node import REMOVED, ContextNode
from sincpro_framework.context.domain.store import ContextStore
from sincpro_framework.context.infrastructure.shared import SharedValues
from sincpro_framework.context.infrastructure.tree import (
    ROOT,
    child,
    current,
    current_execution,
    entered,
    handed_on,
    recorrelated,
    scope_of,
    shared,
)
from sincpro_framework.sincpro_logger import logger


class Context(dict[Any, Any]):
    """The context as a node of the tree sees it, at the moment it was asked for.

    **A `dict`, so everything that takes one takes it** — `json.dumps`, `**context`, `isinstance`.
    Its items are what the node saw when it was asked for, every node above it folded in: read
    where it is used, `use_context()` / `self.context` again after something changed it.
    """

    __slots__ = ("_node", "_owner", "_writable")

    def __init__(self, node: ContextNode, owner: Any = None, writable: bool = True) -> None:
        """`owner` is the bus a mapping write belongs to — `self.context` is its bus's.
        `writable=False` refuses every write: what `bus.current_context()` answers."""
        super().__init__(node.flattened())
        self._node = node
        self._owner = owner
        self._writable = writable

    def copy(self) -> dict[Any, Any]:  # type: ignore[override]
        """The items as a plain dict."""
        return dict(self)

    def __repr__(self) -> str:
        return f"Context({self._node.level}, {dict(self)!r})"

    # --- writing as a mapping: the scope of the call ---------------------------------------------

    def _writing(self) -> ContextNode:
        if not self._writable:
            raise TypeError(
                "this context is read-only: open a scope with bus.context({...}), or write it "
                "where it runs — self.context, use_context()"
            )
        found = scope_of(self._node, self._owner)
        if found is not None:
            return found
        bus = self._node.nearest(Level.BUS)
        if bus is not None and (self._owner is None or bus.owner is self._owner):
            return bus
        logger.warning(
            "the context was written outside every scope: the process context changed — "
            "open one with bus.context(...) or use_context().scoped(...)"
        )
        return ROOT

    def __setitem__(self, key: Any, value: Any) -> None:
        self._writing().values.set(key, value)
        super().__setitem__(key, value)
        self._identity_written(key, value)

    def _identity_written(self, key: Any, value: Any) -> None:
        """`correlation_id` names the flow from here on: the execution in play takes it. The other
        two are minted by the framework — written here they only name what a root is handed.
        """
        if key == CORRELATION_ID:
            recorrelated(self._node, value)
        elif key in (EXECUTION_ID, CAUSATION_ID) and self.execution is not None:
            logger.warning(
                f"'{key}' was written to the context of a running execution: it is minted by the "
                "framework, and only an entrance's value is taken (for a root execution)"
            )

    def __delitem__(self, key: Any) -> None:
        if key not in self:
            raise KeyError(key)
        scope = self._writing()
        inherited = scope.parent is not None and scope.parent.holding(key) is not None
        if key in scope.own and not inherited:
            scope.values.unset(key)
        else:
            scope.values.set(key, REMOVED)
        super().__delitem__(key)

    def update(self, other: Any = (), **more: Any) -> None:  # type: ignore[override]
        for key, value in dict(other, **more).items():
            self[key] = value

    def __ior__(self, other: Any) -> "Context":  # type: ignore[override]
        self.update(other)
        return self

    def setdefault(self, key: Any, default: Any = None) -> Any:
        if key not in self:
            self[key] = default
        return self[key]

    def pop(self, key: Any, *default: Any) -> Any:  # type: ignore[override]
        if key in self:
            value = self[key]
            del self[key]
            return value
        if default:
            return default[0]
        raise KeyError(key)

    def popitem(self) -> tuple[Any, Any]:
        if not self:
            raise KeyError("popitem(): the context is empty")
        key = next(reversed(self))
        return key, self.pop(key)

    def replace(self, values: Mapping[Any, Any]) -> None:
        """The scope of the call now says exactly `values` — what `self.context = {...}` does."""
        scope = self._writing()
        hidden = {key: REMOVED for key in self if key not in values}
        scope.values.replace({**hidden, **values})
        super().clear()
        super().update(values)

    def clear(self) -> None:
        self.replace({})

    # --- writing this node ---------------------------------------------------------------------

    def set(self, key: Any, value: Any) -> None:
        """This node says `key` — what it runs afterwards sees it; its caller and siblings do not."""
        if not self._writable:
            self._writing()
        self._node.values.set(key, value)
        super().__setitem__(key, value)
        self._identity_written(key, value)

    def unset(self, key: Any) -> None:
        """This node stops saying `key`; what is above it shows through again."""
        if not self._writable:
            self._writing()
        self._node.values.unset(key)
        super().clear()
        super().update(self._node.flattened())

    @contextmanager
    def scoped(
        self,
        values: Mapping[Any, Any] | None = None,
        restore: "str | list[str] | None" = None,
        store: ContextStore | None = None,
        kind: EntrypointKind | None = None,
    ) -> Generator["Context", None, None]:
        """A child scope for the block: `values` over what this node sees — and, first, what a
        store kept under each key of `restore`, in order.

        Opened from any level, the block sees that level only — what the nodes under it said is
        left out — and stays in the execution in play: `use_context().parent.scoped()`,
        `use_context(Level.ENTRYPOINT).scoped()`."""
        given = {**self.restored(restore, store), **standardized(values or {})}
        level = Level.SCOPE if self._node.nearest(Level.ENTRYPOINT) else Level.ENTRYPOINT
        if level is Level.ENTRYPOINT:
            kind = kind or EntrypointKind.DIRECT
        node = ContextNode(level, self._node, given, owner=self._owner, kind=kind)
        running = current_execution()
        if running is not None and self.execution is not running:
            node.execution = running
        with entered(shared(node)):
            yield Context(node, self._owner)

    # --- the tree -------------------------------------------------------------------------------

    @property
    def level(self) -> Level:
        return self._node.level

    @property
    def kind(self) -> EntrypointKind | None:
        """Which entrance opened the flow — read on the entrypoint (`context.entrypoint.kind`)."""
        entrypoint = self._node.nearest(Level.ENTRYPOINT)
        return None if entrypoint is None else entrypoint.kind

    @property
    def label(self) -> str:
        return self._node.label

    @property
    def parent(self) -> "Context | None":
        parent = self._node.parent
        return None if parent is None else Context(parent, self._owner)

    def at(self, level: Level) -> "Context | None":
        """The nearest node of `level` this one stands under — itself included."""
        found = self._node.nearest(level)
        return None if found is None else Context(found, self._owner)

    @property
    def root(self) -> "Context":
        return Context(ROOT, self._owner)

    @property
    def bus(self) -> "Context | None":
        return self.at(Level.BUS)

    @property
    def entrypoint(self) -> "Context | None":
        return self.at(Level.ENTRYPOINT)

    @property
    def application(self) -> "Context | None":
        return self.at(Level.APPLICATION)

    @property
    def feature(self) -> "Context | None":
        return self.at(Level.FEATURE)

    @property
    def hook(self) -> "Context | None":
        return self.at(Level.HOOK)

    @property
    def own(self) -> Mapping[Any, Any]:
        """What this node says itself."""
        return self._node.own

    @property
    def execution(self) -> Execution | None:
        """The execution this node is, or stands in."""
        return next((node.execution for node in self._node.chain() if node.execution), None)

    def origin(self, key: Any) -> Origin | None:
        """Where the value of `key` came from — `None` when nothing says it."""
        holder = self._node.holding(key)
        return None if holder is None else holder.origin()

    def lineage(self) -> list["Context"]:
        """This node and every one above it, up to the process."""
        return [Context(node, self._owner) for node in self._node.chain()]

    # --- beyond the process ----------------------------------------------------------------------

    def to_client(self) -> dict[str, Any]:
        """What a client — the frontend — may hold and send back: the travelling keys, a `Secret`
        as its value."""
        return travelling(self)

    def share(
        self,
        store: ContextStore,
        key: str = "process",
        every: timedelta = timedelta(seconds=5),
    ) -> None:
        """The process level shared by every replica through `store` — read locally, read again
        when its version moved, at most once per `every`; a write is kept for the others. Only the
        root is shared: a flow travels with its message."""
        if self._node is not ROOT:
            raise TypeError(
                f"only the process level is shared across replicas, not a {self._node.level} — "
                "use_context().root.share(store)"
            )
        ROOT.values = SharedValues(store, key, every, seed=ROOT.values.current)
        super().clear()
        super().update(ROOT.flattened())

    def store(self, given: ContextStore | None = None) -> ContextStore | None:
        """The store a keep or a restore uses: the one given, else the nearest kept under
        `ContextStore` — in a scope, on a bus, in the process."""
        if given is not None:
            return given
        return self.get(ContextStore)

    def keep(
        self, key: str, ttl: timedelta | None = None, store: ContextStore | None = None
    ) -> None:
        """What this node sees, kept under `key` — with the flow's chain, so whoever restores it
        continues the flow."""
        found = self._required_store(store)
        found.keep(key, handed_on(_named(self)), ttl)

    def restored(
        self, keys: "str | list[str] | None", store: ContextStore | None = None
    ) -> dict[str, Any]:
        """What a store kept under each of `keys`, merged in order — the last one wins."""
        if not keys:
            return {}
        found = self._required_store(store)
        merged: dict[str, Any] = {}
        for key in [keys] if isinstance(keys, str) else keys:
            kept = found.restore(key)
            if kept is None:
                logger.warning(
                    f"the context '{key}' was restored, but nothing is kept under it"
                )
                continue
            merged.update(kept)
        return standardized(merged)

    def _required_store(self, given: ContextStore | None) -> ContextStore:
        found = self.store(given)
        if found is None:
            raise LookupError(
                "no ContextStore to keep or restore with: pass store=..., or set one where it "
                "should apply — use_context().root.set(ContextStore, store) for the process, "
                "bus.context_store(store) for a bus"
            )
        return found


def _named(values: Mapping[Any, Any]) -> dict[str, Any]:
    """The keys a store or a message can carry — those named by text."""
    return {key: value for key, value in values.items() if isinstance(key, str)}


@overload
def use_context() -> Context: ...


@overload
def use_context(level: Level) -> Context | None: ...


def use_context(level: Level | None = None) -> Context | None:
    """The context in play — from anywhere, with no bus in hand. With a level, that level's node:
    `use_context(Level.ENTRYPOINT)`; `None` when the node in play stands under none."""
    context = Context(current())
    return context if level is None else context.at(level)


@contextmanager
def carrying(
    context: Mapping[str, Any], kind: EntrypointKind = EntrypointKind.DIRECT
) -> Generator[None, None, None]:
    """Every bus executed inside the block starts from `context` — what a caller that is not a bus,
    a cron or a worker, hands the buses it calls."""
    with entered(child(Level.SCOPE, standardized(context), kind=kind)):
        yield
