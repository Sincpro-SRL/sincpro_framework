"""`Context` — the one object the context is, wherever it is reached; `use_context()` reaches it.

    context = use_context()                  in a Feature, an interceptor, a hook, an adapter
    context["tenant_id"]                     the effective value: the nearest node wins
    context.get("lang", "es")
    context[SiatSettings]                    a value stored under its type, settings among them

    context["note"] = "x"                    the scope of the call — as `self.context` always wrote
    context.set("pos_id", 3)                 this node only: what it runs afterwards sees it
    with context.scoped({"tenant_id": "beta"}):     a child scope for a block

    context.level · context.parent · context.root · context.entrypoint · context.application
    context.feature · context.hook · context.bus · context.at(ContextLevel.ENTRYPOINT)
    context.own · context.origin("tenant_id") · context.lineage() · context.execution
    context.drafts() · context.drafts(by=["session_id"])   saved where the context is saved
    context.store_in_use()                                   the store drafts, runs and leases use

**Two ways to write, on purpose.** As a mapping — `context[key] = value`, `update`, `del` — a write
lands on the scope of the call: the `bus.context(...)` block, or the call itself; every execution of
that call sees it, the way `self.context` always behaved. `set` writes the node in play only — a
Feature's own value, reaching what it runs and never its siblings or its caller.

`self.context` in a handler and `bus.current_context()` are this same object.
"""

from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from datetime import timedelta
from typing import Any, overload

from sincpro_framework.context.adapters.stores import in_memory_contexts
from sincpro_framework.context.domain.execution import Execution
from sincpro_framework.context.domain.keys import ContextKey
from sincpro_framework.context.domain.level import ContextLevel, EntrypointKind, Origin
from sincpro_framework.context.domain.node import REMOVED, ContextNode
from sincpro_framework.context.domain.owner import Owner
from sincpro_framework.context.domain.store import ContextStore
from sincpro_framework.context.infrastructure.shared import SharedValues
from sincpro_framework.context.infrastructure.travel import travelling
from sincpro_framework.context.infrastructure.tree import ROOT, current, entered
from sincpro_framework.context.services.drafts import Drafts
from sincpro_framework.context.services.executions import current_execution
from sincpro_framework.context.services.nodes import entered_child, shared
from sincpro_framework.context.services.sessions import DEFAULT_IDLE_FOR, Sessions
from sincpro_framework.data_layer.store import Leases
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
        """`Context(feature, {...})` — the node's level and what it sees."""
        return f"Context({self._node.level}, {dict(self)!r})"

    # --- writing as a mapping: the scope of the call ---------------------------------------------

    def _writing(self) -> ContextNode:
        """The node a write as a mapping lands on.

        Context: `context[key] = value` has always meant "for the whole call", so it lands on
        the call's scope, not on the Feature's own node — `set` is the way to write that one.

        1. A read-only context (`bus.current_context()`) refuses: `TypeError`.
        2. The nearest entrypoint or scope — of this context's bus, when it has one.
        3. Else the bus's own node, when it is this context's bus.
        Final: else the process — warned, since a write outside every scope reaches every
        flow.
        """
        if not self._writable:
            raise TypeError(
                "this context is read-only: open a scope with bus.context({...}), or write it "
                "where it runs — self.context, use_context()"
            )
        found = self._node.scope(self._owner)
        if found is not None:
            return found
        bus = self._node.nearest(ContextLevel.BUS)
        if bus is not None and (self._owner is None or bus.owner is self._owner):
            return bus
        logger.warning(
            "the context was written outside every scope: the process context changed — "
            "open one with bus.context(...) or use_context().scoped(...)"
        )
        return ROOT

    def __setitem__(self, key: Any, value: Any) -> None:
        """`key` written on the scope of the call — every execution of that call reads it; a
        `correlation_id` written renames the flow of the execution in play."""
        self._writing().values.set(key, value)
        super().__setitem__(key, value)
        self._identity_written(key, value)

    @property
    def execution(self) -> Execution | None:
        """The execution this node is, or stands in."""
        return next((node.execution for node in self._node.chain() if node.execution), None)

    def _identity_written(self, key: Any, value: Any) -> None:
        """`correlation_id` names the flow from here on: the execution in play takes it. The other
        two are minted by the framework — written here they only name what a root is handed.
        """
        if key == ContextKey.CORRELATION_ID:
            self._node.recorrelate(value)
        elif (
            key in (ContextKey.EXECUTION_ID, ContextKey.CAUSATION_ID)
            and self.execution is not None
        ):
            logger.warning(
                f"'{key}' was written to the context of a running execution: it is minted by the "
                "framework, and only an entrance's value is taken (for a root execution)"
            )

    def __delitem__(self, key: Any) -> None:
        """`key` taken away from the scope of the call — `KeyError` when the context does not
        see it.

        1. The scope said it itself and nothing above says it: the scope stops saying it.
        2. Otherwise — inherited from above — the scope hides it with `REMOVED`, so the call
           no longer sees it while the node above still holds it for everyone else.
        Final: gone from this `Context`'s items too.
        """
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
        """Each key written as `context[key] = value` does — on the scope of the call."""
        for key, value in dict(other, **more).items():
            self[key] = value

    def __ior__(self, other: Any) -> "Context":  # type: ignore[override]
        """`context |= {...}` — `update`, answering this same context."""
        self.update(other)
        return self

    def setdefault(self, key: Any, default: Any = None) -> Any:
        """The value of `key`; when the context does not see it, `default` is written on the
        scope of the call first."""
        if key not in self:
            self[key] = default
        return self[key]

    def pop(self, key: Any, *default: Any) -> Any:  # type: ignore[override]
        """The value of `key`, taken away from the scope of the call as `del` does — the
        default when the context does not see it, `KeyError` without one.

            context.pop("note")          ->  "x", and the scope of the call no longer says it
            context.pop("absent", None)  ->  None
            context.pop("absent")        ->  KeyError("absent")

        A value inherited from above is hidden from the scope, not deleted where it lives."""
        if key in self:
            value = self[key]
            del self[key]
            return value
        if default:
            return default[0]
        raise KeyError(key)

    def popitem(self) -> tuple[Any, Any]:
        """The last key seen and its value, taken away as `pop` does — `KeyError` when
        empty."""
        if not self:
            raise KeyError("popitem(): the context is empty")
        key = next(reversed(self))
        return key, self.pop(key)

    def replace(self, values: Mapping[Any, Any]) -> None:
        """The scope of the call now says exactly `values` — what `self.context = {...}` does.

            context   {"tenant_id": "acme", "lang": "es"}      # tenant_id from the entrypoint
            context.replace({"company": 3})
            context   ->  {"company": 3}       # tenant_id and lang hidden, as REMOVED

        Every key seen now and not in `values` is hidden on the scope — not deleted above."""
        scope = self._writing()
        hidden = {key: REMOVED for key in self if key not in values}
        scope.values.replace({**hidden, **values})
        super().clear()
        super().update(values)

    # --- writing this node ---------------------------------------------------------------------

    def clear(self) -> None:
        """The scope of the call says nothing any more — `replace({})`: what is inherited is
        hidden, not deleted above."""
        self.replace({})

    def set(self, key: Any, value: Any) -> None:
        """This node says `key` — what it runs afterwards sees it; its caller and siblings do not.

            use_context().set("pos_id", 3)     # in IssueInvoice's feature node
            use_context()["pos_id"]   ->  3, here and in the use cases it calls
            the caller, after it      ->  KeyError("pos_id")

        A `correlation_id` written here renames the flow of the execution in play, as `[]=`
        does; an `execution_id` or a `causation_id` is only warned about."""
        if not self._writable:
            self._writing()
        self._node.values.set(key, value)
        super().__setitem__(key, value)
        self._identity_written(key, value)

    def unset(self, key: Any) -> None:
        """This node stops saying `key`; what is above it shows through again.

            entrypoint {"lang": "es"} → this node {"lang": "en"}
            context.unset("lang")   ->  context["lang"]  ->  "es"

        Unlike `del`, nothing is hidden: the context is read again from the chain."""
        if not self._writable:
            self._writing()
        self._node.values.unset(key)
        super().clear()
        super().update(self._node.flattened())

    # --- the tree -------------------------------------------------------------------------------

    def store(self, given: ContextStore | None = None) -> ContextStore | None:
        """The `ContextStore` this context saves in: the one given, else the nearest set — on
        a scope, on a bus (`bus.store_context_in`), on the process (`store_context_in`) — else
        `None`.

        Context: drafts, pipeline runs, leases, caches and idempotency records live where the
        context lives; each component asks here instead of being configured on its own.

            use_context().store()                  ->  KeyValueContexts(...)    billing's Redis
            use_context().store(InMemoryContexts())  ->  that one
        """
        if given is not None:
            return given
        return self.get(ContextStore)

    @contextmanager
    def scoped(
        self,
        values: Mapping[Any, Any] | None = None,
        kind: EntrypointKind | None = None,
    ) -> Generator["Context", None, None]:
        """A child scope for a `with` block: `values` over what this node sees, for the block
        only.

        Context: a piece of work inside a use case — one tenant of a loop, one line of a batch
        — needs values of its own without touching what the caller sees; the scope ends with
        the block. Opened from any level, the block sees that level only — what the nodes
        under it said is left out — and stays in the execution in play.

            with use_context().scoped({"tenant_id": "acme"}) as scope:
                scope["tenant_id"]                          ->  "acme"
            use_context().get("tenant_id")                  ->  None      outside the block
            with use_context(ContextLevel.ENTRYPOINT).scoped():    the entrypoint's view, nothing under it

        1. The level: a scope under the flow's entrypoint, or the entrypoint itself when none
           is open — `kind` naming the entrance, `DIRECT` when unsaid.
        2. The node, carrying the execution in play, so its spans and metrics stay one
           execution.
        Final: the node entered for the block, answered as a `Context`.
        """
        level, kind = self._node.child_level(kind)
        node = ContextNode(
            level, self._node, dict(values or {}), owner=self._owner, kind=kind
        )
        running = current_execution()
        if running is not None and self.execution is not running:
            node.execution = running
        with entered(shared(node)):
            yield Context(node, self._owner)

    @property
    def level(self) -> ContextLevel:
        """The level of the node this context is — `ContextLevel.FEATURE` in a Feature."""
        return self._node.level

    @property
    def kind(self) -> EntrypointKind | None:
        """Which entrance opened the flow — read on the entrypoint (`context.entrypoint.kind`)."""
        entrypoint = self._node.nearest(ContextLevel.ENTRYPOINT)
        return None if entrypoint is None else entrypoint.kind

    @property
    def label(self) -> str:
        """What the node is named after: the bus, the use case or the hook."""
        return self._node.label

    @property
    def parent(self) -> "Context | None":
        """The node right above — `None` above the global (or the process when nothing is
        shared)."""
        parent = self._node.parent
        return None if parent is None else Context(parent, self._owner)

    def at(self, level: ContextLevel) -> "Context | None":
        """The nearest node of `level` this one stands under — itself included."""
        found = self._node.nearest(level)
        return None if found is None else Context(found, self._owner)

    @property
    def root(self) -> "Context":
        """The process level — where its defaults and flags live
        (`root.share(store, "process:billing")` shares it across replicas)."""
        return Context(ROOT, self._owner)

    @property
    def bus(self) -> "Context | None":
        """The bus's node — what it publishes for every execution of its own."""
        return self.at(ContextLevel.BUS)

    @property
    def entrypoint(self) -> "Context | None":
        """The entrypoint of the flow — what the entrance knew: who, which tenant, which
        kind."""
        return self.at(ContextLevel.ENTRYPOINT)

    @property
    def application(self) -> "Context | None":
        """The node of the nearest ApplicationService execution — `None` outside one."""
        return self.at(ContextLevel.APPLICATION)

    @property
    def feature(self) -> "Context | None":
        """The node of the nearest Feature execution — `None` outside one."""
        return self.at(ContextLevel.FEATURE)

    @property
    def hook(self) -> "Context | None":
        """The node of the repository hook running — `None` outside one."""
        return self.at(ContextLevel.HOOK)

    # --- beyond the process ----------------------------------------------------------------------

    @property
    def own(self) -> Mapping[Any, Any]:
        """What this node says itself."""
        return self._node.own

    def origin(self, key: Any) -> Origin | None:
        """Where the value of `key` came from — `None` when nothing says it."""
        holder = self._node.holding(key)
        return None if holder is None else holder.origin()

    def lineage(self) -> list["Context"]:
        """This node and every one above it, up to the process."""
        return [Context(node, self._owner) for node in self._node.chain()]

    def flow(self) -> dict[str, Any]:
        """What crosses a process from here — a message, a request, another service, the
        frontend: the flow, from this node up to its entrypoint. The global, the process and the
        bounded context's level stay; the other side reads its own.

            use_context().flow()   ->  {"tenant_id": "acme", "user_id": "ana", "carrier": "dhl"}
                                       # not the process's "lang", not billing's "plan"
        """
        return self._node.flow()

    def to_client(self) -> dict[str, Any]:
        """What a client — the frontend — may hold and send back: the flow's travelling keys
        (`flow`), a `Secret` as its value. What the backend owns — the process, a bounded
        context's level, the caches, the drafts — stays in the backend."""
        return travelling(self.flow())

    def share(
        self,
        store: ContextStore,
        key: str,
        every: timedelta = timedelta(seconds=5),
    ) -> None:
        """The process level shared by every replica through `store` under `key` — read locally,
        read again when its version moved, at most once per `every`; a write is saved for the
        others. Only the root is shared: a flow travels with its message.

            use_context().root.share(KeyValueContexts(redis), "process:billing")
                ->  ROOT reads and writes through "process:billing" in that store, seeded with
                    what the process already said
            use_context().share(store, "process:billing")   ->  TypeError, from any node but
                                                                 the root

        Context: `key` names the service, the way a sharing bus keys it (`process:<service>`):
        two services that share one key on one store read and write one process level. The
        root's values are replaced by `SharedValues` for the life of the process."""
        if self._node is not ROOT:
            raise TypeError(
                f"only the process level is shared across replicas, not a {self._node.level} — "
                "use_context().root.share(store, key)"
            )
        ROOT.values = SharedValues(store, key, every, seed=ROOT.values.current)
        super().clear()
        super().update(ROOT.flattened())

    def store_in_use(self, given: ContextStore | None = None) -> ContextStore:
        """Where what the context owns is saved — drafts, pipeline runs, leases: the store given,
        else the nearest `ContextStore` — a bus's, the process's — else this process's memory,
        said once.

            use_context().store_in_use()                    ->  billing's KeyValueContexts
            use_context().store_in_use()                    ->  this process's memory, with none set
        """
        found = self.store(given)
        return found if found is not None else in_memory_contexts()

    def drafts(
        self, by: Sequence[str] = Owner.BY_TENANT_AND_USER, store: ContextStore | None = None
    ) -> Drafts:
        """The drafts of whoever this context says — its tenant and its user by default, any keys
        of it with `by` (`["session_id"]`) — saved where the context is saved."""
        return Drafts(self.store_in_use(store), self, by)

    def leases(self, store: ContextStore | None = None) -> Leases:
        """The leases saved where the context is saved — a name held by one owner at a time, by
        every replica that shares the store."""
        return self.store_in_use(store).leases()

    def sessions(
        self,
        idle_for: timedelta | None = DEFAULT_IDLE_FOR,
        at_most: timedelta | None = None,
        namespace: str = "sessions",
        store: ContextStore | None = None,
    ) -> Sessions:
        """The sessions saved where the context is saved — a login that lasts while it is used,
        read by every replica that shares the store.

            use_context().sessions()                          30 days unused, renewed at each use
            use_context().sessions(idle_for=None)             until closed
            use_context().sessions(timedelta(hours=8), at_most=timedelta(days=7), "admin")

        `idle_for` is how long one lasts unused, `at_most` however much it is used — see
        `Sessions`."""
        kept = self.store_in_use(store).key_value_store()
        return Sessions(kept, idle_for, at_most, namespace)


@overload
def use_context() -> Context:
    """The context in play."""


@overload
def use_context(level: ContextLevel) -> Context | None:
    """The node of `level` above the one in play — `None` when there is none."""


def use_context(level: ContextLevel | None = None) -> Context | None:
    """The context in play — from anywhere, with no bus in hand. With a level, that level's node:
    `use_context(ContextLevel.ENTRYPOINT)`; `None` when the node in play stands under none."""
    context = Context(current())
    return context if level is None else context.at(level)


@contextmanager
def carrying(
    context: Mapping[str, Any], kind: EntrypointKind = EntrypointKind.DIRECT
) -> Generator[None, None, None]:
    """Every bus executed inside the block starts from `context` — what a caller that is not a bus,
    a cron or a worker, hands the buses it calls."""
    with entered_child(ContextLevel.SCOPE, dict(context), kind=kind):
        yield
