"""`ContextNode`: one node of the context's tree — its own values and its parent; never one context
overwritten.

    process                       {lang: "es"}
     └─ entrypoint                {tenant_id: "acme"}
         └─ feature IssueInvoice  {}                   reads lang "es" and tenant_id "acme"

**Reading walks up and the nearest node wins**, a React provider's rule. **A node's values are an
immutable mapping replaced whole on a write** — copy-on-write under a lock per node — so a reader in
another thread sees the mapping before the write or after it, never one half written; a write never
reaches the parent.

**A node may share its values with others** (`Values`): a bus's published values are one, read
live by every execution of that bus wherever it was called from.
"""

import threading
from collections.abc import Iterator, Mapping
from types import MappingProxyType
from typing import Any

from sincpro_framework.context.domain.execution import Execution
from sincpro_framework.context.domain.level import EntrypointKind, Level, Origin

_EMPTY: Mapping[Any, Any] = MappingProxyType({})


class _Removed:
    """A key a scope took away from what it inherited — `del context[key]` on an inherited key."""

    def __repr__(self) -> str:
        """`REMOVED` — what a node's values show for a key it took away."""
        return "REMOVED"


REMOVED: Any = _Removed()


class Values:
    """A node's values: read without a lock, replaced whole under one."""

    __slots__ = ("_current", "_writing")

    def __init__(self, values: Mapping[Any, Any] | None = None) -> None:
        """Start with a read-only copy of `values` — the shared empty mapping when none — and
        the lock writers take."""
        self._current: Mapping[Any, Any] = (
            MappingProxyType(dict(values)) if values else _EMPTY
        )
        self._writing = threading.Lock()

    @property
    def current(self) -> Mapping[Any, Any]:
        """The values as they are now — an immutable snapshot a reader keeps using safely
        while a writer replaces it."""
        return self._current

    def set(self, key: Any, value: Any) -> None:
        """`key` takes `value` — a new mapping replaces the old one whole."""
        with self._writing:
            self._current = MappingProxyType({**self._current, key: value})

    def update(self, values: Mapping[Any, Any]) -> None:
        """Every key of `values` takes its value in one replacement — no reader sees half of
        them; nothing to write takes no lock."""
        if not values:
            return
        with self._writing:
            self._current = MappingProxyType({**self._current, **values})

    def unset(self, key: Any) -> None:
        """`key` is gone from this node — a key it does not hold changes nothing. Unlike
        writing `REMOVED`, what a parent says of it shows through again."""
        with self._writing:
            if key in self._current:
                self._current = MappingProxyType(
                    {name: value for name, value in self._current.items() if name != key}
                )

    def replace(self, values: Mapping[Any, Any]) -> None:
        """This node says exactly `values` now — what it said before is dropped."""
        with self._writing:
            self._current = MappingProxyType(dict(values)) if values else _EMPTY


class ContextNode:
    """One node: its level, its own values, its parent, and what it is named after."""

    __slots__ = ("level", "parent", "label", "owner", "kind", "execution", "values", "key")

    def __init__(
        self,
        level: Level,
        parent: "ContextNode | None",
        values: "Mapping[Any, Any] | Values | None" = None,
        label: str = "",
        owner: Any = None,
        kind: EntrypointKind | None = None,
        execution: Execution | None = None,
    ) -> None:
        """A node at `level` under `parent`.

        `values` given as a `Values` is shared, not copied — a bus's published values are one
        object every execution of that bus reads live; a mapping is copied into a `Values` of
        its own. `kind` says which entrance opened the flow (an entrypoint only); `execution`
        is the identity of the execution this node is (an application or feature node only).

            ContextNode(Level.ENTRYPOINT, root, {"tenant_id": "acme"}, label="rest")
                ->  its own Values {"tenant_id": "acme"}, no store key
            ContextNode(Level.BUS, root, billing_values, owner=billing)
                ->  `billing_values` itself, read live by every execution of billing

        `key` starts `None`: the node lives in this process until a store keeps it.
        """
        self.level = level
        self.parent = parent
        self.label = label
        self.owner = owner
        """The bus this node belongs to — `None` for the process and for a scope opened by code
        beside the buses."""
        self.kind = kind
        self.execution = execution
        self.values = values if isinstance(values, Values) else Values(values)
        self.key: str | None = None
        """Where a store keeps this node — `None` when it lives in this process only."""

    @property
    def own(self) -> Mapping[Any, Any]:
        """What this node says itself — never what it inherits."""
        return {
            key: value for key, value in self.values.current.items() if value is not REMOVED
        }

    def chain(self) -> Iterator["ContextNode"]:
        """This node, then each one above it, up to the process — and the global, when a store
        shares the context."""
        node: ContextNode | None = self
        while node is not None:
            yield node
            node = node.parent

    def holding(self, key: Any) -> "ContextNode | None":
        """The nearest node that says `key` — `None` when none does, or the nearest took it away.

            feature.holding("tenant_id")   ->  the entrypoint node, which set "acme"
            feature.holding("lang")        ->  None — the application node wrote REMOVED

        Walked from this node up (`chain`); the first that has the key decides."""
        for node in self.chain():
            values = node.values.current
            if key in values:
                return None if values[key] is REMOVED else node
        return None

    def flattened(self) -> dict[Any, Any]:
        """Every value this node sees, the nearest winning.

            root {"lang": "es", "flag": True} → entrypoint {"lang": "en"}
                → feature {"flag": REMOVED, "company": 3}
            feature.flattened()   ->  {"company": 3, "lang": "en"}

        A key the nearest node took away (`REMOVED`) is gone, whatever a parent says."""
        found: dict[Any, Any] = {}
        for node in self.chain():
            for key, value in node.values.current.items():
                found.setdefault(key, value)
        return {key: value for key, value in found.items() if value is not REMOVED}

    def nearest(self, level: Level) -> "ContextNode | None":
        """This node or the closest above it at `level` — `None` when the chain has none: a
        call with no entrypoint above it is the head of its flow."""
        return next((node for node in self.chain() if node.level is level), None)

    def origin(self) -> Origin:
        """Where a value set on this node comes from: its level and label, and the execution
        it belongs to — the nearest execution up the chain, `None` above every execution.

            feature node of IssueInvoice  ->  Origin(Level.FEATURE, "IssueInvoice", "01J…")
            the process                   ->  Origin(Level.ROOT, "process", None)
        """
        execution = next((node.execution for node in self.chain() if node.execution), None)
        return Origin(self.level, self.label, execution.execution_id if execution else None)

    def __repr__(self) -> str:
        """`ContextNode(feature, 'IssueInvoice', {...})` — its level, label and own values."""
        return f"ContextNode({self.level}, {self.label!r}, {dict(self.own)!r})"
