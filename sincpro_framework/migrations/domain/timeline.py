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

from sincpro_framework.migrations.domain.step import Step

_last = (0, 0)
_last_lock = Lock()


def new_step_id() -> str:
    """A UUIDv7 in hex: 48 bits of Unix milliseconds, the version, 74 bits of counter, the
    variant — always after the last one this process made, even within one millisecond.

    Context: the counter starts at a random value with room to grow, and within one millisecond
    (or when the clock steps back) it only increments — RFC 9562 §6.2, method 1.
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
