"""The context keys the framework itself reads and writes.

    ContextKey.USER_ID          "user_id"          who the execution acts for
    ContextKey.TENANT_ID        "tenant_id"        the tenant it acts in
    ContextKey.TENANT_IDS       "tenant_ids"       every tenant it may act across — a list
    ContextKey.EXECUTION_ID     "execution_id"     the execution's identity
    ContextKey.CAUSATION_ID     "causation_id"     the execution that caused it
    ContextKey.CORRELATION_ID   "correlation_id"   the flow it belongs to
    ContextKey.LANG             "lang"             the language of the call
    ContextKey.PREFERRED_LANGS  "preferred_langs"  the languages the caller prefers, best first
    ContextKey.PIPELINE_CONTEXT       the pipeline run a step belongs to, and its runs in the store
    ContextKey.ENTITY_DRAFT_CONTEXT   the drafts in the store
    CONTEXT_NODE                      the node in play, handed on when a store shares the context

A member is its text: `context[ContextKey.USER_ID]` and `context["user_id"]` read the same key.

What of a context crosses a process is `sincpro_framework.context.domain.travel`; how it is
written on a transport, `sincpro_framework.context.adapters.propagation`.

The standard library only: the context package imports this, and nothing here imports a bus.
"""

from collections.abc import Mapping
from enum import StrEnum
from typing import Any


class ContextKey(StrEnum):
    """The context keys the framework itself reads and writes."""

    USER_ID = "user_id"
    TENANT_ID = "tenant_id"
    TENANT_IDS = "tenant_ids"
    EXECUTION_ID = "execution_id"
    CAUSATION_ID = "causation_id"
    CORRELATION_ID = "correlation_id"
    LANG = "lang"
    """The language of the call, a BCP 47 tag (`es-BO`) — what a `MultiLanguage` and a message
    are read in. What sets it wins over what the caller prefers: the call carried it, a
    provider gave it, a level above holds it."""
    PREFERRED_LANGS = "preferred_langs"
    """The languages the caller prefers, best first — what an entrance read off
    `Accept-Language`: `["es-BO", "es", "en"]`. The first one is the call's language when
    nothing set `lang`."""
    PIPELINE_CONTEXT = "SINCPRO_PIPELINE_CONTEXT"
    """The run a pipeline's steps belong to — `{"pipeline": …, "run_id": …}` on the node of its
    execution, so a run at the same time is another branch; in the store, the namespace of every
    run."""
    ENTITY_DRAFT_CONTEXT = "SINCPRO_ENTITY_DRAFT_CONTEXT"
    """In the store, the namespace every draft is saved under, apart from everything else."""
    SKIP_CHECKS = "skip_checks"
    """Why the saves of this call skip the entities' `@check` rules — an import, a migration —
    as a sentence that is logged with who asked: `bus.context(skip_checks="legacy import")`.
    Computations still run; only the rules are not judged."""


def language_of(context: Mapping[Any, Any]) -> str | None:
    """The language a context's call is in: its `lang`, else the first language its caller
    prefers, else none — the default text.

        language_of({"lang": "en", "preferred_langs": ["es"]})   ->  "en"
        language_of({"preferred_langs": ["es-BO", "es"]})        ->  "es-BO"
        language_of({})                                          ->  None
    """
    language = context.get(ContextKey.LANG)
    if language:
        return str(language)
    preferred = context.get(ContextKey.PREFERRED_LANGS) or ()
    return str(preferred[0]) if preferred else None


PLUMBING = frozenset({"trace_id", "span_id", "carrier"})
"""Keys that carry the trace between buses — it travels in its own W3C headers, not with these."""

CONTEXT_NODE = "sincpro.context_node"
"""The node in play, handed on beside the identity when a store shares the context: a receiving bus
that set the same store reads the sender's chain from there. A key of the context handed on, not a
header — `x-context-node` carries it on a transport (`adapters.propagation`)."""
