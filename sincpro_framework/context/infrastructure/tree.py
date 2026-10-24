"""The node in play — per thread and per async task — and the process's root.

    current()                       the node in play; the root outside every scope
    entered(node)                   that node in play until the block ends
    opened_execution(dto, …)        a node for one execution of a use case, its identity minted
    current_execution()             the execution in play, wherever it is read from

`contextvars` answers one question — which node is current — and does it per thread and per async
task: an asyncio task starts from a copy of its creator's, a thread from none on the default build
(`entrypoint.threads` hands it on). The tree itself is `domain.node`.

Only the standard library and the domain: the buses import this, nothing here imports a bus.
"""

from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from types import MappingProxyType
from typing import Any, cast

from sincpro_framework.common.ids import new_entity_id
from sincpro_framework.context.domain.execution import CONTEXT_NODE, Execution, chained
from sincpro_framework.context.domain.keys import ContextKey
from sincpro_framework.context.domain.level import SCOPES, EntrypointKind, Level
from sincpro_framework.context.domain.node import ContextNode
from sincpro_framework.context.infrastructure.distributed import sharing_of

ROOT = ContextNode(Level.ROOT, None, label="process")
"""The process: one per interpreter, the parent of every flow."""

_current: ContextVar[ContextNode | None] = ContextVar("sincpro_context_node", default=None)


def current() -> ContextNode:
    """The node in play; the process's root outside every scope."""
    return _current.get() or ROOT


@contextmanager
def entered(node: ContextNode) -> Generator[ContextNode, None, None]:
    """`node` is the node in play for the block — for this thread or task only — and what was
    in play before comes back when it ends, raised or not.

        with entered(ContextNode(Level.SCOPE, current(), {"lang": "es"})):
            current().flattened()["lang"]   ->  "es"
    """
    token = _current.set(node)
    try:
        yield node
    finally:
        _current.reset(token)


def shared(node: ContextNode, node_id: str | None = None) -> ContextNode:
    """`node`, kept in the store of the nearest bus that shares its context — as it is, when none
    does. Free when no bus of the process shares: only a shared process has a global above it.

        shared(node)            ->  node, untouched — no bus of the process shares
        shared(node, "01J9")    ->  node kept as "node:01J9", under billing's store
        shared(node)            ->  node kept under a new entity id

    The same node object is answered either way: being kept changes how it writes, not what
    it is."""
    if ROOT.parent is None:
        return node
    sharing = sharing_of(node)
    if sharing is None:
        return node
    return sharing.kept(node, node_id or new_entity_id())


def child(
    level: Level,
    values: Mapping[Any, Any] | None = None,
    label: str = "",
    owner: Any = None,
    kind: EntrypointKind | None = None,
) -> ContextNode:
    """A node under the one in play — an entrypoint when no entrance opened the flow yet; kept in
    the store of the bus above it, when that bus shares its context."""
    parent = current()
    if level is Level.SCOPE and parent.nearest(Level.ENTRYPOINT) is None:
        level, kind = Level.ENTRYPOINT, kind or EntrypointKind.DIRECT
    return shared(ContextNode(level, parent, values, label, owner, kind))


def executing_context() -> Mapping[str, Any]:
    """Everything the node in play sees, read-only — empty outside every scope but the process's."""
    return MappingProxyType(current().flattened())


# --- the execution in play ----------------------------------------------------------------------


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


def _identified(dto: Any, new_id: Callable[[], str]) -> tuple[str, str | None, str]:
    """The identity of an execution about to open: its own id, its cause, its flow.

    Context: one rule decides whether an execution continues a chain or heads one — an event
    it runs, a use case that called it, or an entrance that was handed an identity by a
    caller.

    1. The cause: the event being executed; else the execution that called this one; else
       what the flow was handed — an entrypoint, a remote caller, a message.
    2. The flow: the event's; else what the context says — a scope or a write can name it — else
       the caller's.
    3. Its own id: what the flow was handed, for a root only; else a new one.
    4. Final: `(execution_id, causation_id, correlation_id)` by `chained`."""
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
    causation_id, correlation_id = chained(cause_id, correlation, own)
    return own, causation_id, correlation_id


def recorrelated(node: ContextNode, correlation_id: Any) -> None:
    """The execution `node` stands in now belongs to the flow `correlation_id` — what a write of
    `correlation_id` to the context means: its signals and what it runs afterwards say it."""
    for one in node.chain():
        if one.execution is not None:
            one.execution = replace(one.execution, correlation_id=str(correlation_id))
            return


@contextmanager
def opened_execution(
    dto: Any,
    bus: str,
    level: Level,
    new_id: Callable[[], str] = new_entity_id,
    owner: Any = None,
) -> Generator[ContextNode, None, None]:
    """The node of one execution of `dto` on `bus`, in play until the block ends.

        with opened_execution(CreateInvoice(...), "billing", Level.FEATURE) as node:
            node.execution   ->  Execution(execution_id="01J9", causation_id="01J8",
                                           correlation_id="01J7", use_case="CreateInvoice",
                                           bus="billing", level=Level.FEATURE)
                                 # called by execution 01J8 of flow 01J7
            node.label       ->  "CreateInvoice"

    Its identity by the one rule (`_identified`); kept in the store when its bus shares the
    context, under its own `execution_id`."""
    own, causation_id, correlation_id = _identified(dto, new_id)
    execution = Execution(own, causation_id, correlation_id, type(dto).__name__, bus, level)
    node = ContextNode(level, current(), label=execution.use_case, owner=owner)
    node.execution = execution
    shared(node, own)
    with entered(node):
        yield node


# --- what goes on -------------------------------------------------------------------------------


def handed_on(context: Mapping[str, Any]) -> dict[str, Any]:
    """`context` as an execution elsewhere receives it — another service, a message: caused by
    the execution in play, in its flow, and — when a store shares the context — pointing at the
    node in play, so a receiver that set the store reads the chain from there. Its own
    `execution_id` stays here.

        handed_on({"tenant_id": "acme", "execution_id": "E1"})     # inside E1 of flow C
            ->  {"tenant_id": "acme", "causation_id": "E1", "correlation_id": "C"}
                plus "sincpro.context_node": "E1" when a store keeps the node
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
    kept = next((node.key for node in current().chain() if node.key), None)
    if kept is not None:
        passed[CONTEXT_NODE] = kept
    return passed


def chain_for(event: Any) -> dict[str, str]:
    """What a new event recorded, published or saved now lacks of the execution producing it:
    `causation_id` that execution, `correlation_id` its flow. Empty outside an execution, for an
    event that already says both, and for one already stored — history keeps its own chain.

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


def scope_of(node: ContextNode, owner: Any = None) -> ContextNode | None:
    """The scope a write to the context as a mapping lands on — the nearest entrypoint or scope,
    of `owner` when one is named."""
    for one in node.chain():
        if one.level in SCOPES and (owner is None or one.owner is owner):
            return one
    return None


# --- what the signals read ----------------------------------------------------------------------


def live_context() -> Mapping[Any, Any]:
    """Everything the node in play sees, as it is now — what every signal of an execution reads."""
    return MappingProxyType(current().flattened())


def hidden_keys() -> frozenset[str]:
    """What the bus running the execution keeps off every signal (`hide_in_logs`).

        hidden_keys()   ->  frozenset({"password", "card_number"})   # billing hides them
        hidden_keys()   ->  frozenset()                             # outside every bus

    The nearest bus up the chain that declares some decides; the others are not merged."""
    for node in current().chain():
        hidden = getattr(node.owner, "_hidden_in_logs", None)
        if hidden is not None:
            return frozenset(hidden)
    return frozenset()
