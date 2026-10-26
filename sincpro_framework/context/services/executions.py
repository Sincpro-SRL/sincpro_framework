"""The execution in play: opened with its identity minted, read from anywhere, handed on, and the
chain a new event joins.

    with opened_execution(dto, "billing", ContextLevel.FEATURE) as node:   one execution of a use case
    current_execution()                                                    the execution in play
    handed_on(context)                                                     what an execution elsewhere receives
    chain_for(event)                                                       what a new event lacks of its chain
"""

from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from typing import Any, cast

from sincpro_framework.common.ids import new_entity_id
from sincpro_framework.context.domain.execution import Chain, Execution
from sincpro_framework.context.domain.keys import CONTEXT_NODE, ContextKey
from sincpro_framework.context.domain.level import ContextLevel
from sincpro_framework.context.domain.node import ContextNode
from sincpro_framework.context.infrastructure.tree import current, entered
from sincpro_framework.context.services.nodes import shared


def current_execution() -> Execution | None:
    """The execution in play — from a Feature, an interceptor, a hook or a component beside the
    buses; `None` outside one."""
    for node in current().chain():
        if node.execution is not None:
            return node.execution
    return None


def _text(value: Any) -> str | None:
    """A context value as an id: text, or `None` when it is unsaid or empty."""
    return None if value is None or value == "" else str(value)


def _event_cause(dto: Any) -> tuple[str, str | None] | None:
    """The event being executed, when it is one: it is the cause of the execution it starts."""
    cause = getattr(dto, "execution_cause", None)
    return cast("tuple[str, str | None]", cause()) if callable(cause) else None


def _given(node: ContextNode, key: str) -> Any:
    """What the nearest node holding `key` says of it — `None` when no node says it, or the
    nearest took it away."""
    holder = node.holding(key)
    return None if holder is None else holder.own[key]


def _identified(dto: Any, new_id: Callable[[], str]) -> tuple[str, Chain]:
    """The identity of an execution about to open: its own id and its chain.

    Context: one rule decides whether an execution continues a chain or heads one — an event
    it runs, a use case that called it, or an entrance that was handed an identity by a
    caller.

    1. The cause: the event being executed; else the execution that called this one; else
       what the flow was handed — an entrypoint, a remote caller, a message.
    2. The flow: the event's; else what the context says — a scope or a write can name it — else
       the caller's.
    3. Its own id: what the flow was handed, for a root only; else a new one.
    Final: `(execution_id, Chain(causation_id, correlation_id))` by `Chain.after`."""
    node = current()
    parent = current_execution()
    said = _text(_given(node, ContextKey.CORRELATION_ID))
    event = _event_cause(dto)
    if event is not None:
        cause_id, correlation = event
        correlation = correlation or said or (parent.correlation_id if parent else None)
        own = new_id()
    elif parent is not None:
        cause_id, correlation, own = (
            parent.execution_id,
            said or parent.correlation_id,
            new_id(),
        )
    else:
        cause_id = _text(_given(node, ContextKey.CAUSATION_ID))
        correlation = said
        own = _text(_given(node, ContextKey.EXECUTION_ID)) or new_id()
    return own, Chain.after(cause_id, correlation, own)


@contextmanager
def opened_execution(
    dto: Any,
    bus: str,
    level: ContextLevel,
    new_id: Callable[[], str] = new_entity_id,
    owner: Any = None,
) -> Generator[ContextNode, None, None]:
    """The node of one execution of `dto` on `bus`, in play until the block ends.

        with opened_execution(CreateInvoice(...), "billing", ContextLevel.FEATURE) as node:
            node.execution   ->  Execution(execution_id="01J9", causation_id="01J8",
                                           correlation_id="01J7", use_case="CreateInvoice",
                                           bus="billing", level=ContextLevel.FEATURE)
                                 # called by execution 01J8 of flow 01J7
            node.label       ->  "CreateInvoice"

    Its identity by the one rule (`_identified`); saved in the store when its bus shares the
    context, under its own `execution_id`."""
    own, chain = _identified(dto, new_id)
    execution = Execution(
        own, chain.causation_id, chain.correlation_id, type(dto).__name__, bus, level
    )
    node = ContextNode(level, current(), label=execution.use_case, owner=owner)
    node.execution = execution
    shared(node, own)
    with entered(node):
        yield node


def handed_on(context: Mapping[str, Any]) -> dict[str, Any]:
    """`context` as an execution elsewhere receives it — another service, a message: caused by
    the execution in play, in its flow, and — when a store shares the context — pointing at the
    node in play, so a receiver that set the store reads the chain from there. Its own
    `execution_id` stays here.

        handed_on({"tenant_id": "acme", "execution_id": "E1"})     # inside E1 of flow C
            ->  {"tenant_id": "acme", "causation_id": "E1", "correlation_id": "C"}
                plus "sincpro.context_node": "E1" when a store saves the node
        handed_on({"tenant_id": "acme"})                            # outside any execution
            ->  {"tenant_id": "acme"}

    A `sincpro.context_node` handed in is dropped and named again from this side."""
    passed = {
        key: value
        for key, value in context.items()
        if key not in (ContextKey.EXECUTION_ID, CONTEXT_NODE)
    }
    running = current_execution()
    if running is not None:
        passed.update(running.handed_on())
    saved = next((node.key for node in current().chain() if node.key), None)
    if saved is not None:
        passed[CONTEXT_NODE] = saved
    return passed


def chain_for(event: Any) -> dict[str, str]:
    """What a new event recorded, published or saved now lacks of the execution producing it:
    `causation_id` that execution, `correlation_id` its flow. Empty outside an execution, for an
    event that already says both, and for one already stored — history has its own chain.

        chain_for(InvoiceIssued(...))                     # new, inside E1 of flow C
            ->  {"causation_id": "E1", "correlation_id": "C"}
        chain_for(InvoiceIssued(correlation_id="C0"))     ->  {"causation_id": "E1"}
        chain_for(an event loaded from the store)         ->  {}

    It only answers what is missing; setting it on the event is the caller's."""
    running = current_execution()
    if running is None or not getattr(event, "is_new", False):
        return {}
    missing: dict[str, str] = {}
    if not event.causation_id:
        missing[ContextKey.CAUSATION_ID] = running.execution_id
    if not event.correlation_id:
        missing[ContextKey.CORRELATION_ID] = running.correlation_id
    return missing
