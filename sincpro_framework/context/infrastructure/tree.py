"""The node in play — per thread and per async task — and the process's root.

    current()                       the node in play; the root outside every scope
    entered(node)                   that node in play until the block ends

`contextvars` answers one question — which node is current — and does it per thread and per async
task: an asyncio task starts from a copy of its creator's, a thread from none on the default build
(`entrypoint.threads` hands it on). The tree itself is `domain.node`; what is done with the node in
play — a child under it, an execution opened, the context handed on — is `services`.

Only the standard library and the domain: the buses import this, nothing here imports a bus.
"""

from contextvars import ContextVar, Token
from types import TracebackType

from sincpro_framework.context.domain.level import ContextLevel
from sincpro_framework.context.domain.node import ContextNode

ROOT = ContextNode(ContextLevel.ROOT, None, label="process")
"""The process: one per interpreter, the parent of every flow."""

_current: ContextVar[ContextNode | None] = ContextVar("sincpro_context_node", default=None)


def current() -> ContextNode:
    """The node in play; the process's root outside every scope."""
    return _current.get() or ROOT


class entered:
    """`node` is the node in play for the block — for this thread or task only — and what was
    in play before comes back when it ends, raised or not.

        with entered(ContextNode(ContextLevel.SCOPE, current(), {"lang": "es"})):
            current().flattened()["lang"]   ->  "es"

    Context: a class, as `contextlib.suppress` is, not a generator — it is entered once per
    execution, scope and hook, once per row for a hook that reads, and a generator costs twice
    as much."""

    __slots__ = ("node", "_token")

    def __init__(self, node: ContextNode) -> None:
        """Holds `node`; nothing is in play until the block is entered."""
        self.node = node
        self._token: Token[ContextNode | None] | None = None

    def __enter__(self) -> ContextNode:
        """`node` in play, answered for `as`."""
        self._token = _current.set(self.node)
        return self.node

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        """What was in play before, back — whatever ended the block, which is raised on."""
        if self._token is not None:
            _current.reset(self._token)
