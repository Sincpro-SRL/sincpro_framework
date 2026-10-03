"""The context keys the framework itself reads and writes, and what of a context crosses a process.

    USER_ID      "user_id"      who the execution acts for
    TENANT_ID    "tenant_id"    the tenant it acts in
    TENANT_IDS   "tenant_ids"   every tenant it may act across — a list

    standardized({"user.id": "ana"})   →  {"user.id": "ana", "user_id": "ana"}    warned once
    travelling(context)                →  the keys a header can carry

**One name per key.** `"user.id"` and `"tenant"` were taught before these; they are still read, as
the standard key, with a warning the first time — the key given stays where it was, so nothing that
reads it breaks.

**What travels is what is simple**: text, numbers, booleans, and lists of them — `tenant_ids`, an
idempotency key, the identity, the keys a project adds — and a `Secret` holding one, as its value:
an API key the next context needs reaches it. What goes in the context is the project's decision;
the framework filters nothing. An object, a connection or a callable stays in its process, with a
warning the first time, never an error. How it is written on a transport is
`sincpro_framework.context.adapters.propagation`.

The standard library and pydantic only: the context package imports this, and nothing here imports
a bus.
"""

from collections.abc import Mapping
from typing import Any

from pydantic import Secret, SecretStr

from sincpro_framework.sincpro_logger import logger

USER_ID = "user_id"
TENANT_ID = "tenant_id"
TENANT_IDS = "tenant_ids"

LEGACY_USER_ID = "user.id"
LEGACY_TENANT = "tenant"
LEGACY_KEYS = {LEGACY_USER_ID: USER_ID, LEGACY_TENANT: TENANT_ID}
"""Keys taught before the standard ones, read as them."""

PLUMBING = frozenset({"trace_id", "span_id", "carrier"})
"""Keys that carry the trace between buses — it travels in its own W3C headers, not with these."""

_SIMPLE = (str, int, float, bool)
_warned: set[str] = set()


def _warn_once(key: str, message: str) -> None:
    if key not in _warned:
        _warned.add(key)
        logger.warning(message)


def standardized(context: Mapping[str, Any]) -> dict[str, Any]:
    """`context` with the standard key beside each legacy one it carries, when it lacks it."""
    found = dict(context)
    for legacy, standard in LEGACY_KEYS.items():
        if legacy in found and standard not in found:
            found[standard] = found[legacy]
            _warn_once(
                legacy,
                f"the context key '{legacy}' is read as '{standard}' — write '{standard}'",
            )
    return found


def _simple(value: Any) -> bool:
    if isinstance(value, _SIMPLE):
        return True
    return isinstance(value, (list, tuple)) and all(isinstance(one, _SIMPLE) for one in value)


def travelling(context: Mapping[str, Any]) -> dict[str, Any]:
    """The keys of `context` a header can carry: simple values — a `Secret` as the value it
    holds — the trace's plumbing left to its own headers. What cannot travel is warned about once
    per key and stays here."""
    found: dict[str, Any] = {}
    for key, value in context.items():
        if isinstance(value, (Secret, SecretStr)):
            value = value.get_secret_value()
        if not isinstance(key, str) or key in PLUMBING or value is None:
            continue
        if _simple(value):
            found[key] = list(value) if isinstance(value, tuple) else value
        else:
            _warn_once(
                f"travel:{key}",
                f"the context key '{key}' holds a {type(value).__name__}: it stays in this "
                "process — only text, numbers, booleans and lists of them travel",
            )
    return found
