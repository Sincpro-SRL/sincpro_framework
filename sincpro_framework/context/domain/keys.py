"""The context keys the framework itself reads and writes.

    ContextKey.USER_ID          "user_id"          who the execution acts for
    ContextKey.TENANT_ID        "tenant_id"        the tenant it acts in
    ContextKey.TENANT_IDS       "tenant_ids"       every tenant it may act across — a list
    ContextKey.EXECUTION_ID     "execution_id"     the execution's identity
    ContextKey.CAUSATION_ID     "causation_id"     the execution that caused it
    ContextKey.CORRELATION_ID   "correlation_id"   the flow it belongs to
    ContextKey.PIPELINE_CONTEXT       the pipeline run a step belongs to, and its runs in the store
    ContextKey.ENTITY_DRAFT_CONTEXT   the drafts in the store
    CONTEXT_NODE                      the node in play, handed on when a store shares the context

A member is its text: `context[ContextKey.USER_ID]` and `context["user_id"]` read the same key.

What of a context crosses a process is `sincpro_framework.context.domain.travel`; how it is
written on a transport, `sincpro_framework.context.adapters.propagation`.

The standard library only: the context package imports this, and nothing here imports a bus.
"""

from enum import StrEnum


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
    """In the store, the namespace every draft is saved under, apart from everything else."""


PLUMBING = frozenset({"trace_id", "span_id", "carrier"})
"""Keys that carry the trace between buses — it travels in its own W3C headers, not with these."""

CONTEXT_NODE = "sincpro.context_node"
"""The node in play, handed on beside the identity when a store shares the context: a receiving bus
that set the same store reads the sender's chain from there. A key of the context handed on, not a
header — `x-context-node` carries it on a transport (`adapters.propagation`)."""
