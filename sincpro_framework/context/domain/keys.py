"""The context keys the framework itself reads and writes, and what of a context crosses a process.

    ContextKey.USER_ID          "user_id"          who the execution acts for
    ContextKey.TENANT_ID        "tenant_id"        the tenant it acts in
    ContextKey.TENANT_IDS       "tenant_ids"       every tenant it may act across — a list
    ContextKey.EXECUTION_ID     "execution_id"     the execution's identity
    ContextKey.CAUSATION_ID     "causation_id"     the execution that caused it
    ContextKey.CORRELATION_ID   "correlation_id"   the flow it belongs to
    ContextKey.PIPELINE_CONTEXT       the pipeline run a step belongs to, and its runs in the store
    ContextKey.ENTITY_DRAFT_CONTEXT   the drafts in the store

    travelling(context)   →  the keys a header can carry

A member is its text: `context[ContextKey.USER_ID]` and `context["user_id"]` read the same key.

**What travels is what is simple**: text, numbers, booleans, lists of them — `tenant_ids`, an
idempotency key, the identity, the keys a project adds — a mapping of those by text, one level deep
(`SINCPRO_PIPELINE_CONTEXT`), and a `Secret` holding one, as its value:
an API key the next context needs reaches it. What goes in the context is the project's decision;
the framework filters nothing. An object, a connection or a callable stays in its process, with a
warning the first time, never an error. How it is written on a transport is
`sincpro_framework.context.adapters.propagation`.

The standard library and pydantic only: the context package imports this, and nothing here imports
a bus.
"""

from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from pydantic import Secret, SecretStr

from sincpro_framework.sincpro_logger import logger


class ContextKey(StrEnum):
    """The context keys the framework itself reads and writes."""

    USER_ID = "user_id"
    TENANT_ID = "tenant_id"
    TENANT_IDS = "tenant_ids"
    EXECUTION_ID = "execution_id"
    CAUSATION_ID = "causation_id"
    CORRELATION_ID = "correlation_id"
    PIPELINE_CONTEXT = "SINCPRO_PIPELINE_CONTEXT"
    """The run a pipeline's steps belong to — `{"pipeline": …, "run_id": …}` on the node of its
    execution, so a run at the same time is another branch; in the store, the namespace of every
    run."""
    ENTITY_DRAFT_CONTEXT = "SINCPRO_ENTITY_DRAFT_CONTEXT"
    """In the store, the namespace every draft is kept under, apart from everything else."""


PLUMBING = frozenset({"trace_id", "span_id", "carrier"})
"""Keys that carry the trace between buses — it travels in its own W3C headers, not with these."""

_SIMPLE = (str, int, float, bool)
_warned: set[str] = set()


def _warn_once(key: str, message: str) -> None:
    if key not in _warned:
        _warned.add(key)
        logger.warning(message)


def _simple(value: Any) -> bool:
    if isinstance(value, _SIMPLE):
        return True
    return isinstance(value, (list, tuple)) and all(isinstance(one, _SIMPLE) for one in value)


def _travels(value: Any) -> bool:
    """A simple value, or a mapping of them by text — one level, as JSON carries it."""
    if isinstance(value, Mapping):
        return all(isinstance(key, str) and _simple(one) for key, one in value.items())
    return _simple(value)


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
        if _travels(value):
            if isinstance(value, Mapping):
                found[key] = {
                    name: list(one) if isinstance(one, tuple) else one
                    for name, one in value.items()
                }
            else:
                found[key] = list(value) if isinstance(value, tuple) else value
        else:
            _warn_once(
                f"travel:{key}",
                f"the context key '{key}' holds a {type(value).__name__}: it stays in this "
                "process — only text, numbers, booleans, lists of them and a mapping of those "
                "travel",
            )
    return found
