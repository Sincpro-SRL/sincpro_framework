"""The timeline: every chain merged into one order, and the id a new step takes.

Context: each chain keeps its own order — the manifest's, whatever its ids say — a step goes
after the steps it `requires`, and among the steps free to go the smallest id goes first. Ids
are UUIDv7, so that is the oldest: chains that share a store interleave the way their steps were
written. A UUIDv7 says when a step was made, not what it needs; `requires` says that.
"""

import heapq
import os
import time
from collections.abc import Sequence
from threading import Lock

from sincpro_framework.data_layer.migrations.domain.step import Step

_last = (0, 0)
_last_lock = Lock()


def new_step_id() -> str:
    """A UUIDv7 in hex: 48 bits of Unix milliseconds, the version, 74 bits of counter, the
    variant — always after the last one this process made, even within one millisecond.

    Context: the counter starts at a random value with room to grow, and within one millisecond
    (or when the clock steps back) it only increments — RFC 9562 §6.2, method 1.

        new_step_id()   ->  "01a16ec29864702f9f105f09cfbae1a5"
        new_step_id()   ->  "01a16ec29864702f9f105f09cfbae1a6"   the same millisecond, +1

    32 hex digits, the 13th the version `7`; ids made later sort after, as text too.
    """
    global _last
    milliseconds = time.time_ns() // 1_000_000
    with _last_lock:
        last_milliseconds, last_counter = _last
        if milliseconds <= last_milliseconds:
            milliseconds, counter = last_milliseconds, last_counter + 1
        else:
            counter = int.from_bytes(os.urandom(9)) >> 2
        _last = (milliseconds, counter)
    value = (
        milliseconds << 80
        | 0x7 << 76
        | (counter >> 62) << 64
        | 0x2 << 62
        | counter & ((1 << 62) - 1)
    )
    return f"{value:032x}"


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

    Context: each chain keeps its own order whatever its ids say, a step goes after what it
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
