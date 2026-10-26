"""The context saved in a store — the same tree, each level saved where the one who owns it said:
the process in the process's store, a bounded context in its own.

    store_context_in(KeyValueContexts(RedisKeyValue(redis)))              the process
    billing.store_context_in(KeyValueContexts(RedisKeyValue(billing_redis)))   a bounded context

    global          "global"                    the process's store: every service that set it
     └ process      "process:<service>"         the process's store: this service's replicas
        └ bus       "bus:<name>"                the bounded context's store: its N pods
           └ entrypoint → application → feature → hook → scope
                    "node:<id>" + "node:<id>:meta"     the bounded context's store: one node per
                                                       execution, written through

A bounded context may grow into a service of its own: its level and its executions never live in
another context's store, nor in the process's — a Redis of its own is one line in its own wiring.

**Only an id travels.** Handing the context on — the remote bus, a message (`inject`) — carries
`sincpro.context_node`, the node in play. A receiving bus that set the store opens its entrance
under the sender's nodes, read back from the store: what the sender's ApplicationService wrote is
what the receiver's Feature reads, and a value written after the hand-off is read fresh. The
sender's nodes are read here, never written; the sender's process never reaches the receiver.

**Reads stay local.** A node of this execution is written through on each write; the sender's
nodes, the bounded context's level, the process and the global are refreshed by their version at
most once per `every`.

Only the standard library and the domain: the buses import this, nothing here imports a bus.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sincpro_framework.context.domain.execution import CHAIN_KEYS
from sincpro_framework.context.domain.level import ContextLevel
from sincpro_framework.context.domain.node import ContextNode, ContextSharing, Values
from sincpro_framework.context.domain.store import ContextStore
from sincpro_framework.context.infrastructure.shared import SharedValues

GLOBAL_KEY = "global"
PROCESS_EVERY = timedelta(seconds=1)
"""How often a replica reads again what another wrote on the process and the global levels."""
CHAIN_LIMIT = 64
"""Nodes read back from a sender's chain at most — a guard against a cycle in what was saved."""


class WrittenThrough(Values):
    """A node of this execution: read locally, saved in the store on each write."""

    __slots__ = ("_store", "_key", "_ttl")

    def __init__(
        self,
        store: ContextStore,
        key: str,
        ttl: timedelta,
        values: Mapping[Any, Any] | None = None,
    ) -> None:
        """Hold `values` locally and save them under `key` for `ttl` — at once when there are
        any, so a receiver handed this node finds what it already says.

            WrittenThrough(store, "node:01J…", timedelta(hours=1), {"tenant_id": "acme"})
                ->  store.save("node:01J…", {"tenant_id": "acme"}, 1h) at once
            WrittenThrough(store, "node:01J…", timedelta(hours=1))   ->  nothing saved yet

        Only the keys that are text are saved (`named`); one under a type stays here."""
        super().__init__(values)
        self._store = store
        self._key = key
        self._ttl = ttl
        if values:
            self._saved()

    def _saved(self) -> None:
        """Save what this node says now under its key — the whole mapping, its TTL renewed."""
        self._store.save(self._key, self.named(), self._ttl)

    def set(self, key: Any, value: Any) -> None:
        """Written locally, then saved in the store."""
        super().set(key, value)
        self._saved()

    def update(self, values: Mapping[Any, Any]) -> None:
        """Written locally, then saved in the store."""
        super().update(values)
        self._saved()

    def unset(self, key: Any) -> None:
        """Taken away locally, then saved in the store."""
        super().unset(key)
        self._saved()

    def replace(self, values: Mapping[Any, Any]) -> None:
        """Replaced locally, then saved in the store."""
        super().replace(values)
        self._saved()


class ReadBack(SharedValues):
    """A node of another execution — the sender's — read from the store, never written here."""

    def _refused(self) -> None:
        """Every write here is a mistake: `TypeError`, saying where to write instead."""
        raise TypeError(
            "this node belongs to the execution that handed the context on: it is read here, "
            "never written — write your own, self.context[...] or use_context().set(...)"
        )

    def set(self, key: Any, value: Any) -> None:
        """Refused: the sender's node is read here, never written."""
        self._refused()

    def update(self, values: Mapping[Any, Any]) -> None:
        """Refused: the sender's node is read here, never written."""
        self._refused()

    def unset(self, key: Any) -> None:
        """Refused: the sender's node is read here, never written."""
        self._refused()

    def replace(self, values: Mapping[Any, Any]) -> None:
        """Refused: the sender's node is read here, never written."""
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
        """Where a node's values are saved: `node_key("01J…")  ->  "node:01J…"` — its meta
        beside them, under `"node:01J…:meta"`."""
        return f"node:{node_id}"

    def saved(self, node: ContextNode, node_id: str) -> ContextNode:
        """`node` under `node_id`: its values written through, its place in the chain beside them.

        1. The meta: the nearest saved node above it, its level, its label.
        2. Final: its values written through from now on.

            sharing.saved(feature_node, "01J9")
                ->  "node:01J9:meta" = {"parent": "01J8", "level": "feature",
                                        "label": "IssueInvoice"};  "node:01J9" = its values
                    and `feature_node.key == "01J9"`

        The same node object is answered, now saved: its `key` set, its values replaced by a
        `WrittenThrough` seeded with what it said."""
        above = node.parent
        parent = next((one.key for one in above.chain() if one.key), None) if above else None
        self.store.save(
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
        """The nodes the store holds from `node_id` up — read back, never written — hung under
        `top`; `top` itself when the store holds none of them.

        1. Each node's meta, up its `parent`, until none is saved or the chain repeats.
        2. Final: the nodes rebuilt outermost first, each one's values read back from the store.

            sharing.chain_from("01J9", root)     # saved: 01J9 → 01J8 (its parent) → none
                ->  feature "01J9"  under  application "01J8"  under  root, both `ReadBack`
            sharing.chain_from("gone", root)     ->  root

        At most `CHAIN_LIMIT` (64) nodes are read; a level the meta lacks is `ContextLevel.SCOPE`.
        """
        found: list[tuple[str, Mapping[str, Any]]] = []
        seen: set[str] = set()
        following = node_id
        while following and following not in seen and len(found) < CHAIN_LIMIT:
            meta = self.store.load(f"{self.node_key(following)}:meta")
            if meta is None:
                break
            found.append((following, meta))
            seen.add(following)
            following = str(meta.get("parent") or "")
        node = top
        for one, meta in reversed(found):
            node = ContextNode(
                ContextLevel(meta.get("level", ContextLevel.SCOPE.value)),
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

        1. Nothing handed on: the entrance opens under `top`, saying all of `values`.
        2. The sender's chain is read back from the store and hung under `top`.
        3. Every key a node of that chain says is the store's to answer — read fresh from
           there.
        Final: the chain's innermost node, and of `values` only the identity (`CHAIN_KEYS`)
        and the keys the chain does not say.

            entrance({"tenant_id": "acme", "correlation_id": "c1"}, "01J…", root)
            ->  (sender's feature node, {"correlation_id": "c1"})    the chain says tenant_id
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


def sharing_of(node: ContextNode | None) -> ContextSharing | None:
    """How the nearest bus above `node` shares its context — `None` when it does not.

        sharing_of(feature_node_of_billing)   ->  SharedContext(store=KeyValueContexts(...),
                                                    ttl=..., every=...)
        sharing_of(root)                      ->  None      # no bus above the process
        sharing_of(None)                      ->  None

    Read off each node's bus up the chain — its `shared_context`, the first one set."""
    if node is None:
        return None
    for one in node.chain():
        if one.owner is not None and one.owner.shared_context is not None:
            return one.owner.shared_context
    return None


def joined(root: ContextNode, store: ContextStore, every: timedelta, service: str) -> None:
    """The process `root` joins the process's store (`store_context_in`): its own values saved
    for this service's replicas, the global node above it shared by every service that set the
    store. What is joined already stays as it is.

        joined(ROOT, contexts, timedelta(seconds=1), "sincpro-odoo")
            ->  ROOT reads and writes "process:sincpro-odoo", its parent the global "global"

    Context: a process has one process level and one global, so they live on the process's
    store — never on a bounded context's, which holds that context's own level and its
    executions (`bus.store_context_in`). One set by hand (`root.share`) is left in place."""
    if not isinstance(root.values, SharedValues):
        root.values = SharedValues(
            store, f"process:{service}", every, seed=root.values.current
        )
    if root.parent is None:
        root.parent = ContextNode(
            ContextLevel.GLOBAL,
            None,
            SharedValues(store, GLOBAL_KEY, every),
            label="global",
        )
