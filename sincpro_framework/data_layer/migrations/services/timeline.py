"""The timeline: every chain merged into one order.

Context: each chain runs in its own order — the manifest's, whatever its ids say — a step goes
after the steps it `requires`, and among the steps free to go the smallest id goes first. Ids
are UUIDv7, so that is the oldest: chains that share a store interleave the way their steps were
written. A UUIDv7 says when a step was made, not what it needs; `requires` says that.
"""

import heapq
from collections.abc import Sequence

from sincpro_framework.data_layer.migrations.domain.step import Step


def _dependencies(chains: Sequence[Sequence[Step]]) -> dict[str, set[str]]:
    """What each step waits for: the step before it in its own chain, and every step it
    `requires`. A `requires` that names a step no chain has is refused as `ValueError`.

        chains [[a1, a2], [b1 (requires a2)]]
        ->  {"x/a/a1": set(), "x/a/a2": {"x/a/a1"}, "x/b/b1": {"x/a/a2"}}
    """
    known = {step.key for chain in chains for step in chain}
    dependencies: dict[str, set[str]] = {}
    for chain in chains:
        for position, step in enumerate(chain):
            unknown = [key for key in step.requires if key not in known]
            if unknown:
                raise ValueError(
                    f"{step.key} requires {', '.join(unknown)}, which no chain has"
                )
            before = {chain[position - 1].key} if position else set()
            dependencies[step.key] = before | set(step.requires)
    return dependencies


def timeline(chains: Sequence[Sequence[Step]]) -> list[Step]:
    """Every chain merged into the one order the steps run in — a topological sort where,
    among the steps free to go, the smallest id (the oldest UUIDv7) goes first.

    Context: each chain runs in its own order whatever its ids say, a step goes after what it
    `requires`, and chains sharing a store interleave the way their steps were written.

        chains [[a1(t=1), a2(t=4)], [b1(t=2), b2(t=3, requires a2)]]
        ->  [a1, b1, a2, b2]

    1. Each step's dependencies (`_dependencies`), and for each step the steps it unblocks.
    2. The steps with no dependency go into a heap keyed by id.
    3. Pop the smallest id, append it, and release every step it unblocks whose dependencies
       are now all done.
    Final: when some steps were never released they wait on each other — refused as
    `ValueError` naming them; otherwise the order.
    """
    steps = {step.key: step for chain in chains for step in chain}
    waiting = _dependencies(chains)
    unblocks: dict[str, list[str]] = {key: [] for key in steps}
    for key, needs in waiting.items():
        for need in needs:
            unblocks[need].append(key)
    ready = [(step.id, step.key) for step in steps.values() if not waiting[step.key]]
    heapq.heapify(ready)
    ordered: list[Step] = []
    while ready:
        _, key = heapq.heappop(ready)
        ordered.append(steps[key])
        for other in unblocks[key]:
            waiting[other].discard(key)
            if not waiting[other]:
                heapq.heappush(ready, (steps[other].id, other))
    if len(ordered) != len(steps):
        stuck = sorted(key for key, needs in waiting.items() if needs)
        raise ValueError(f"the steps {', '.join(stuck)} require each other in a cycle")
    return ordered
