"""What of a context a header can carry, and the warning — once per key in the process — for what
stays here.

    travelling(context)   →  the keys a header can carry (`domain.travel.Travel`)

Context: the rule of what travels is the domain's (`Travel.of`); saying it once per key, however
many requests a context crosses on, is the process's mechanism — every codec, every header and the
frontend's copy share the one record of what was warned about.
"""

from collections.abc import Mapping
from typing import Any

from sincpro_framework.context.domain.travel import Travel
from sincpro_framework.sincpro_logger import logger

_warned: set[str] = set()
"""The keys already warned about in this process — a context crossing on every request must not
flood the log with the same warning."""


def travelling(context: Mapping[Any, Any]) -> dict[str, Any]:
    """The keys of `context` a header can carry: simple values — a `Secret` as the value it
    holds — the trace's plumbing left to its own headers. What cannot travel is warned about once
    per key and stays here.

        travelling({"tenant_id": "acme", "token": Secret("t0k"), "db": connection})
            ->  {"tenant_id": "acme", "token": "t0k"}
                and once: "the context key 'db' holds a Connection: it stays in this …"

    1. The context split by what travels (`Travel.of`).
    2. Each key that stays, not warned about yet in this process, is warned about.
    Final: what travels, in the context's order.
    """
    travel = Travel.of(context)
    for key, kind in travel.stays.items():
        if f"travel:{key}" not in _warned:
            _warned.add(f"travel:{key}")
            logger.warning(
                f"the context key '{key}' holds a {kind}: it stays in this "
                "process — only text, numbers, booleans, lists of them and a mapping of those "
                "travel"
            )
    return travel.travels
