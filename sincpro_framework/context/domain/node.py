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
        return "REMOVED"


REMOVED: Any = _Removed()


class Values:
    """A node's values: read without a lock, replaced whole under one."""

    __slots__ = ("_current", "_writing")

    def __init__(self, values: Mapping[Any, Any] | None = None) -> None:
        self._current: Mapping[Any, Any] = (
            MappingProxyType(dict(values)) if values else _EMPTY
        )
        self._writing = threading.Lock()

    @property
    def current(self) -> Mapping[Any, Any]:
        return self._current

    def set(self, key: Any, value: Any) -> None:
        with self._writing:
            self._current = MappingProxyType({**self._current, key: value})

    def update(self, values: Mapping[Any, Any]) -> None:
        if not values:
            return
        with self._writing:
            self._current = MappingProxyType({**self._current, **values})

    def unset(self, key: Any) -> None:
        with self._writing:
            if key in self._current:
                self._current = MappingProxyType(
                    {name: value for name, value in self._current.items() if name != key}
                )

    def replace(self, values: Mapping[Any, Any]) -> None:
        with self._writing:
            self._current = MappingProxyType(dict(values)) if values else _EMPTY


class ContextNode:
    """One node: its level, its own values, its parent, and what it is named after."""

    __slots__ = ("level", "parent", "label", "owner", "kind", "execution", "values")

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
        self.level = level
        self.parent = parent
        self.label = label
        self.owner = owner
        """The bus this node belongs to — `None` for the process and for a scope opened by code
        beside the buses."""
        self.kind = kind
        self.execution = execution
        self.values = values if isinstance(values, Values) else Values(values)

    @property
    def own(self) -> Mapping[Any, Any]:
        """What this node says itself — never what it inherits."""
        return {
            key: value for key, value in self.values.current.items() if value is not REMOVED
        }

    def chain(self) -> Iterator["ContextNode"]:
        """This node, then each one above it, up to the process."""
        node: ContextNode | None = self
        while node is not None:
            yield node
            node = node.parent

    def holding(self, key: Any) -> "ContextNode | None":
        """The nearest node that says `key` — `None` when none does, or the nearest took it away."""
        for node in self.chain():
            values = node.values.current
            if key in values:
                return None if values[key] is REMOVED else node
        return None

    def flattened(self) -> dict[Any, Any]:
        """Every value this node sees, the nearest winning."""
        found: dict[Any, Any] = {}
        for node in self.chain():
            for key, value in node.values.current.items():
                found.setdefault(key, value)
        return {key: value for key, value in found.items() if value is not REMOVED}

    def nearest(self, level: Level) -> "ContextNode | None":
        return next((node for node in self.chain() if node.level is level), None)

    def origin(self) -> Origin:
        execution = next((node.execution for node in self.chain() if node.execution), None)
        return Origin(self.level, self.label, execution.execution_id if execution else None)

    def __repr__(self) -> str:
        return f"ContextNode({self.level}, {self.label!r}, {dict(self.own)!r})"
