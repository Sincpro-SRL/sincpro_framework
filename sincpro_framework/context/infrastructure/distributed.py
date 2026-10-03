"""The context kept in a store — the same tree, its nodes kept where every service that set the
store reads them.

    billing.context_store(KeyValueContexts(RedisKeyValue(redis)))    said by each bus that shares

    global          "global"                    every service and replica that set the store
     └ process      "process:<service>"         this service's replicas, never another service
        └ bus       — kept nowhere: what a bus publishes is its own
           └ entrypoint → application → feature → hook → scope
                    "node:<id>" + "node:<id>:meta"     one node per execution, written through

**Only an id travels.** Handing the context on — the remote bus, a message (`inject`) — carries
`sincpro.context_node`, the node in play. A receiving bus that set the store opens its entrance
under the sender's nodes, read back from the store: what the sender's ApplicationService wrote is
what the receiver's Feature reads, and a value written after the hand-off is read fresh. The
sender's nodes are read here, never written; the sender's process never reaches the receiver.

**Reads stay local.** A node of this execution is written through on each write; the sender's
nodes, the process and the global are refreshed by their version at most once per `every`.

Only the standard library and the domain: the buses import this, nothing here imports a bus.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sincpro_framework.context.domain.execution import CHAIN_KEYS
from sincpro_framework.context.domain.level import Level
from sincpro_framework.context.domain.node import ContextNode, Values
from sincpro_framework.context.domain.store import ContextStore
from sincpro_framework.context.infrastructure.shared import SharedValues

GLOBAL_KEY = "global"
CHAIN_LIMIT = 64
"""Nodes read back from a sender's chain at most — a guard against a cycle in what was kept."""


def _named(values: Mapping[Any, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if isinstance(key, str)}


class WrittenThrough(Values):
    """A node of this execution: read locally, kept in the store on each write."""

    __slots__ = ("_store", "_key", "_ttl")

    def __init__(
        self,
        store: ContextStore,
        key: str,
        ttl: timedelta,
        values: Mapping[Any, Any] | None = None,
    ) -> None:
        super().__init__(values)
        self._store = store
        self._key = key
        self._ttl = ttl
        if values:
            self._kept()

    def _kept(self) -> None:
        self._store.keep(self._key, _named(self._current), self._ttl)

    def set(self, key: Any, value: Any) -> None:
        super().set(key, value)
        self._kept()

    def update(self, values: Mapping[Any, Any]) -> None:
        super().update(values)
        self._kept()

    def unset(self, key: Any) -> None:
        super().unset(key)
        self._kept()

    def replace(self, values: Mapping[Any, Any]) -> None:
        super().replace(values)
        self._kept()


class ReadBack(SharedValues):
    """A node of another execution — the sender's — read from the store, never written here."""

    def _refused(self) -> None:
        raise TypeError(
            "this node belongs to the execution that handed the context on: it is read here, "
            "never written — write your own, self.context[...] or use_context().set(...)"
        )

    def set(self, key: Any, value: Any) -> None:
        self._refused()

    def update(self, values: Mapping[Any, Any]) -> None:
        self._refused()

    def unset(self, key: Any) -> None:
        self._refused()

    def replace(self, values: Mapping[Any, Any]) -> None:
        self._refused()


@dataclass(frozen=True)
class SharedContext:
    """How a bus shares its context: the store, how long a node lives, how often what others
    wrote is read again."""

    store: ContextStore
    ttl: timedelta
    every: timedelta

    @staticmethod
    def node_key(node_id: str) -> str:
        return f"node:{node_id}"

    def kept(self, node: ContextNode, node_id: str) -> ContextNode:
        """`node` under `node_id`: its values written through, its place in the chain beside them.

        1. The meta: the nearest kept node above it, its level, its label.
        2. Final: its values written through from now on.
        """
        above = node.parent
        parent = next((one.key for one in above.chain() if one.key), None) if above else None
        self.store.keep(
            f"{self.node_key(node_id)}:meta",
            {"parent": parent or "", "level": node.level.value, "label": node.label},
            self.ttl,
        )
        node.key = node_id
        node.values = WrittenThrough(
            self.store, self.node_key(node_id), self.ttl, node.values.current
        )
        return node

    def chain_from(self, node_id: str, top: ContextNode) -> ContextNode:
        """The nodes the store keeps from `node_id` up — read back, never written — hung under
        `top`; `top` itself when the store keeps none of them.

        1. Each node's meta, up its `parent`, until none is kept or the chain repeats.
        2. Final: the nodes rebuilt outermost first, each one's values read back from the store.
        """
        found: list[tuple[str, Mapping[str, Any]]] = []
        seen: set[str] = set()
        following = node_id
        while following and following not in seen and len(found) < CHAIN_LIMIT:
            meta = self.store.restore(f"{self.node_key(following)}:meta")
            if meta is None:
                break
            found.append((following, meta))
            seen.add(following)
            following = str(meta.get("parent") or "")
        node = top
        for one, meta in reversed(found):
            node = ContextNode(
                Level(meta.get("level", Level.SCOPE.value)),
                node,
                ReadBack(self.store, self.node_key(one), self.every),
                label=str(meta.get("label", "")),
            )
            node.key = one
        return node

    def entrance(
        self, values: Mapping[Any, Any], handed: str | None, top: ContextNode
    ) -> tuple[ContextNode, dict[Any, Any]]:
        """Where an entrance of a bus that shares opens: under the sender's chain when a node was
        handed on — and what of `values` it still says itself: the store is the source of what
        the sender's chain says, the identity always travels with the call.
        """
        if not handed:
            return top, dict(values)
        parent = self.chain_from(handed, top)
        said: set[Any] = set()
        for node in parent.chain():
            if node is top:
                break
            said.update(node.values.current)
        own = {
            key: value
            for key, value in values.items()
            if key in CHAIN_KEYS or key not in said
        }
        return parent, own


def sharing_of(node: ContextNode | None) -> SharedContext | None:
    """How the nearest bus above `node` shares its context — `None` when it does not."""
    if node is None:
        return None
    for one in node.chain():
        sharing = getattr(one.owner, "_shared_context", None)
        if sharing is not None:
            return sharing
    return None


def joined(root: ContextNode, sharing: SharedContext, service: str) -> None:
    """The process `root` joins the store: its own values kept for this service's replicas, the
    global node above it shared by every service that set the store. Once per process."""
    if not isinstance(root.values, SharedValues):
        root.values = SharedValues(
            sharing.store, f"process:{service}", sharing.every, seed=root.values.current
        )
    if root.parent is None:
        root.parent = ContextNode(
            Level.GLOBAL,
            None,
            SharedValues(sharing.store, GLOBAL_KEY, sharing.every),
            label="global",
        )
