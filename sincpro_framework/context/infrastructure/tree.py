"""The node in play — per thread and per async task — and the process's root.

    current()                       the node in play; the root outside every scope
    entered(node)                   that node in play until the block ends

`contextvars` answers one question — which node is current — and does it per thread and per async
task: an asyncio task starts from a copy of its creator's, a thread from none on the default build
(`entrypoint.threads` hands it on). The tree itself is `domain.node`; what is done with the node in
play — a child under it, an execution opened, the context handed on — is `services`.

Only the standard library and the domain: the buses import this, nothing here imports a bus.
"""

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar

from sincpro_framework.context.domain.level import ContextLevel
from sincpro_framework.context.domain.node import ContextNode

ROOT = ContextNode(ContextLevel.ROOT, None, label="process")
"""The process: one per interpreter, the parent of every flow."""

_current: ContextVar[ContextNode | None] = ContextVar("sincpro_context_node", default=None)


def current() -> ContextNode:
    """The node in play; the process's root outside every scope."""
    return _current.get() or ROOT


@contextmanager
def entered(node: ContextNode) -> Generator[ContextNode, None, None]:
    """`node` is the node in play for the block — for this thread or task only — and what was
    in play before comes back when it ends, raised or not.

        with entered(ContextNode(ContextLevel.SCOPE, current(), {"lang": "es"})):
            current().flattened()["lang"]   ->  "es"
    """
    token = _current.set(node)
    try:
        yield node
    finally:
        _current.reset(token)
