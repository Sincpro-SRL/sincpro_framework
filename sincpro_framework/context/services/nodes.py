"""What is done with the node in play: a child opened under it, a node saved where its bus shares
the context, what it sees, and what its bus hides from the signals.

    with entered_child(ContextLevel.HOOK, label="audit"):   a node under the one in play
    shared(node)                                            saved when a bus above shares
    live_context()                                          what the node in play sees, now
    hidden_keys()                                           what its bus hides from every signal
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from sincpro_framework.common.ids import new_entity_id
from sincpro_framework.context.domain.level import ContextLevel, EntrypointKind
from sincpro_framework.context.domain.node import ContextBus, ContextNode
from sincpro_framework.context.infrastructure.distributed import sharing_of
from sincpro_framework.context.infrastructure.tree import current, entered


def shared(node: ContextNode, node_id: str | None = None) -> ContextNode:
    """`node`, saved in the store of the nearest bus that shares its context — as it is, when
    none does.

        shared(node)            ->  node, untouched — no bus above it shares
        shared(node, "01J9")    ->  node saved as "node:01J9", under billing's store
        shared(node)            ->  node saved under a new entity id

    The same node object is answered either way: being saved changes how it writes, not what
    it is. Context: asked of the bus above the node, never of the process — a bounded context
    with a store of its own shares its executions whether the process set one or not."""
    sharing = sharing_of(node)
    if sharing is None:
        return node
    return sharing.saved(node, node_id or new_entity_id())


def entered_child(
    level: ContextLevel,
    values: Mapping[Any, Any] | None = None,
    label: str = "",
    owner: ContextBus | None = None,
    kind: EntrypointKind | None = None,
    saved: bool = True,
) -> entered:
    """A node under the one in play, in play for the block — saved in the store of the bus above
    it, when that bus shares its context and `saved` is left on.

        with entered_child(ContextLevel.HOOK, label="audit") as node:
            node.level   ->  ContextLevel.HOOK
        with entered_child(ContextLevel.SCOPE, {"tenant_id": "acme"}) as node:
            node.level   ->  ContextLevel.ENTRYPOINT, kind DIRECT   # no entrance opened the flow
        with entered_child(ContextLevel.HOOK, label="mask", saved=False):
            ...          # this process only: a moment that runs once per row read

    A scope asked for where no entrance opened the flow yet is that flow's entrypoint
    (`ContextNode.child_level`). A node left unsaved still isolates what is written in it; a
    context handed on from inside carries the nearest saved node above it."""
    parent = current()
    if level is ContextLevel.SCOPE:
        level, kind = parent.child_level(kind)
    node = ContextNode(level, parent, values, label, owner, kind)
    return entered(shared(node) if saved else node)


def live_context() -> Mapping[Any, Any]:
    """Everything the node in play sees, read-only, as it is now — what every signal of an
    execution reads and what a guard judges a permission against; empty outside every scope but
    the process's."""
    return MappingProxyType(current().flattened())


def hidden_keys() -> frozenset[str]:
    """What the bus running the execution hides from every signal (`hide_in_logs`).

        hidden_keys()   ->  frozenset({"password", "card_number"})   # billing hides them
        hidden_keys()   ->  frozenset()                             # outside every bus

    The nearest bus up the chain decides; the others are not merged."""
    for node in current().chain():
        if node.owner is not None:
            return node.owner.hidden_in_logs
    return frozenset()
