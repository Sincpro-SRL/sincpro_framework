"""How several things hung on one point run: hooks on an aggregate, interceptors around a Command,
error handlers on a bus — the one order every extension point of the framework uses.

    ordered([Placement(audit), Placement(check, sequence=5), Placement(stamp, after=(check,))])

Context — the algorithm, and where it comes from:

- **A stable topological sort (Kahn, 1962).** `before` and `after` are hard constraints, as
  systemd's `Before=`/`After=` or Gradle's `mustRunAfter`; among what the constraints leave free,
  the lowest `sequence` goes first — Odoo's `sequence`, WordPress's priority — and then the order
  they were registered. The same input always gives the same order.
- **Priority inheritance.** What must run before an item takes that item's sequence when it is
  lower, so "just before the early one" is early — never stuck behind everything later.
- **A cycle is refused**, naming what is in it: `a before b` and `b before a` has no answer.
- **A constraint on something not registered is no constraint**, as in systemd: it names what
  may be switched off, and an extension must not break because the thing it ran next to is gone.
- **A replacement takes the place of what it replaces** — its registration position, its
  sequence, its constraints, and every constraint others had on it — so replacing one never
  reorders the rest.
- **What is switched off is gone**, with every constraint on it.
- **What works, only not as said, is a note, never a refusal**: replacing what is not there
  registers the replacement on its own; replacing one already replaced replaces the latest;
  switching off what is not there does nothing. The extension point logs the notes.

Items are compared by identity: a class, a function — never a name written as text.
"""

import heapq
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from sincpro_framework.exceptions import ExtensionRefused

DEFAULT_SEQUENCE = 10


def refused(message: str) -> ExtensionRefused:
    return ExtensionRefused(message)


def name_of(item: Any) -> str:
    """`module.qualname` of a function or class — of its class for a callable instance."""
    qualname = getattr(item, "__qualname__", type(item).__qualname__)
    return f"{item.__module__}.{qualname}"


@dataclass(frozen=True)
class Placement:
    item: Any
    sequence: int = DEFAULT_SEQUENCE
    before: tuple[Any, ...] = ()
    after: tuple[Any, ...] = ()
    replaces: Any = None


@dataclass(frozen=True)
class Ordered:
    items: list[Any]
    replaced: dict[Any, tuple[Any, ...]] = field(default_factory=dict)
    """Each replacement in force → what it replaced, oldest first."""
    off: tuple[Any, ...] = ()
    notes: tuple[str, ...] = ()
    """What was asked and could not be done as said — for the extension point to log."""


@dataclass
class _Node:
    item: Any
    index: int
    sequence: int
    before: set[Any]
    after: set[Any]


def _placed(
    placements: Sequence[Placement],
) -> tuple[dict[Any, _Node], dict[Any, tuple[Any, ...]], list[str]]:
    """1. Each registration becomes a node, in registration order.
    2. A replacement takes its target's node — index, sequence, constraints — adding its own.
       2.1 Its target already replaced: it replaces the latest, with a note.
       2.2 Its target not registered: it is a registration of its own, with a note.
    3. Final: constraints that named a replaced item name its replacement.
    """
    nodes: dict[Any, _Node] = {}
    replaced: dict[Any, tuple[Any, ...]] = {}
    successor: dict[Any, Any] = {}
    notes: list[str] = []

    def current(item: Any) -> Any:
        while item in successor:
            item = successor[item]
        return item

    for index, placement in enumerate(placements):
        own = _Node(
            placement.item,
            index,
            placement.sequence,
            set(placement.before),
            set(placement.after),
        )
        target = placement.replaces
        if target is None:
            nodes[placement.item] = own
            continue
        if target in successor:
            latest = current(target)
            notes.append(
                f"{name_of(placement.item)} replaces {name_of(target)}, which "
                f"{name_of(latest)} already replaced — it replaces {name_of(latest)}"
            )
            target = latest
        taken = nodes.pop(target, None)
        if taken is None:
            notes.append(
                f"{name_of(placement.item)} replaces {name_of(target)}, which is not registered "
                "here — it runs as a registration of its own"
            )
            nodes[placement.item] = own
            continue
        successor[target] = placement.item
        replaced[placement.item] = (*replaced.pop(target, ()), target)
        nodes[placement.item] = _Node(
            placement.item,
            taken.index,
            taken.sequence if placement.sequence == DEFAULT_SEQUENCE else placement.sequence,
            taken.before | own.before,
            taken.after | own.after,
        )

    for node in nodes.values():
        node.before = {current(one) for one in node.before}
        node.after = {current(one) for one in node.after}
    return nodes, replaced, notes


def _cycle(edges: dict[Any, set[Any]], left: set[Any]) -> list[Any]:
    """One cycle among the items the sort could not place, for the refusal to name."""
    start = next(iter(left))
    path, seen = [start], {start}
    while True:
        following = next(one for one in edges[path[-1]] if one in left)
        if following in seen:
            return path[path.index(following) :] + [following]
        path.append(following)
        seen.add(following)


def _sorted(
    nodes: dict[Any, _Node],
    edges: dict[Any, set[Any]],
    incoming: dict[Any, int],
    sequence: dict[Any, int],
) -> list[Any]:
    """Kahn's sort, taking the free item with the lowest (sequence, registration index) — and
    refusing, naming it, a cycle that leaves items no order can place."""
    waiting = dict(incoming)
    free = [(sequence[item], nodes[item].index, item) for item in nodes if waiting[item] == 0]
    heapq.heapify(free)
    placed: list[Any] = []
    while free:
        item = heapq.heappop(free)[2]
        placed.append(item)
        for later in edges[item]:
            waiting[later] -= 1
            if waiting[later] == 0:
                heapq.heappush(free, (sequence[later], nodes[later].index, later))
    if len(placed) < len(nodes):
        cycle = _cycle(edges, set(nodes) - set(placed))
        raise refused(
            "these run before one another in a circle, so no order exists: "
            + " → ".join(name_of(item) for item in cycle)
        )
    return placed


def ordered(placements: Sequence[Placement], off: Iterable[Any] = ()) -> Ordered:
    """The order `placements` run in, with `off` switched off.

    1. Replacements resolved into the nodes that stay.
    2. What is switched off removed — a note when it is not registered.
    3. `before`/`after` turned into edges among what is left.
    4. Each item's effective sequence: the lowest of its own and of everything it must run
       before — so what must run just before an early item runs early too, instead of waiting
       behind every later one (priority inheritance, as a scheduler avoids priority inversion).
    5. Final: Kahn's sort by (effective sequence, registration index).
    """
    nodes, replaced, notes = _placed(placements)
    switched_off = tuple(off)
    for item in switched_off:
        if item not in nodes:
            notes.append(f"{name_of(item)} is switched off, but it is not registered here")
            continue
        del nodes[item]
    edges: dict[Any, set[Any]] = {item: set() for item in nodes}
    incoming: dict[Any, int] = {item: 0 for item in nodes}
    for node in nodes.values():
        for later in node.before:
            if later in nodes and later not in edges[node.item]:
                edges[node.item].add(later)
                incoming[later] += 1
        for earlier in node.after:
            if earlier in nodes and node.item not in edges[earlier]:
                edges[earlier].add(node.item)
                incoming[node.item] += 1
    by_registration = _sorted(
        nodes, edges, incoming, {i: n.sequence for i, n in nodes.items()}
    )
    effective = {item: node.sequence for item, node in nodes.items()}
    for item in reversed(by_registration):
        for later in edges[item]:
            effective[item] = min(effective[item], effective[later])
    placed = _sorted(nodes, edges, incoming, effective)
    in_force = {item: chain for item, chain in replaced.items() if item in nodes}
    return Ordered(placed, in_force, switched_off, tuple(notes))
