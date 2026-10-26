"""`context_scopes`: the node in play as the rest of the framework reaches it — what it sees,
what its bus hides, a child opened under it, and what the scopes closed inside an execution
said.

    with context_scopes.child(ContextLevel.HOOK, label="audit"):   a node under the one in play
    context_scopes.live()                  what the node in play sees, now
    context_scopes.flow()                  what of it crosses a process: the flow
    context_scopes.hidden_keys()           what its bus hides from every signal
    with context_scopes.fresh_process():   the process level as a new interpreter has it
    with context_scopes.collecting():      the scopes closing inside the block are remembered
        context_scopes.closed()            what they said, for the signals that close the execution

Context: a guard, a hook, a signal or an envelope of another package never reaches into the tree
of nodes — it asks here.
"""

from collections.abc import Generator, Mapping
from contextlib import AbstractContextManager, contextmanager
from typing import Any

from sincpro_framework.context.domain.level import ContextLevel, EntrypointKind
from sincpro_framework.context.domain.node import ContextBus, ContextNode, Values
from sincpro_framework.context.infrastructure.closed_scopes import CLOSED_SCOPES
from sincpro_framework.context.infrastructure.tree import ROOT, current
from sincpro_framework.context.services.nodes import entered_child, hidden_keys, live_context


class ContextScopes:
    """The node in play and the scopes around it — one object, `scopes`, so nothing outside the
    context reaches into the tree."""

    def child(
        self,
        level: ContextLevel,
        values: Mapping[Any, Any] | None = None,
        label: str = "",
        owner: ContextBus | None = None,
        kind: EntrypointKind | None = None,
        saved: bool = True,
    ) -> AbstractContextManager[ContextNode]:
        """A node of `level` under the one in play, in play for the block — saved where the bus
        above it shares its context, unless `saved=False` keeps it in this process: what runs
        once per row read is no write to the store each.

            with context_scopes.child(ContextLevel.HOOK, label="audit") as node:
                node.level   ->  ContextLevel.HOOK
            with context_scopes.child(ContextLevel.HOOK, label="mask", saved=False):
                ...          # isolated as any node, never written to the store
        """
        return entered_child(level, values, label, owner, kind, saved)

    def live(self) -> Mapping[Any, Any]:
        """Everything the node in play sees, read-only, as it is now — what every signal reads
        and what a guard judges a permission against.

            context_scopes.live()   ->  {"tenant_id": "acme", "user_id": "ana", …}
        """
        return live_context()

    def flow(self) -> dict[str, Any]:
        """What the flow in play says — what crosses a process: never the global, the process
        or a bounded context's level (`ContextNode.flow`).

            context_scopes.flow()   ->  {"tenant_id": "acme", "user_id": "ana"}
        """
        return current().flow()

    def hidden_keys(self) -> frozenset[str]:
        """What the bus running the execution hides from every signal (`hide_in_logs`) — empty
        outside every bus.

            context_scopes.hidden_keys()   ->  frozenset({"password", "card_number"})
        """
        return hidden_keys()

    @contextmanager
    def fresh_process(self) -> Generator[None, None, None]:
        """The process level as a new interpreter has it, for the block — its values a copy of
        what it says now, no store joined, no global above it — and the process as it was put
        back when the block ends, raised or not.

            with context_scopes.fresh_process():
                store_context_in(InMemoryKeyValue())     only this block's
            use_context().root.get(ContextStore)    ->  what it was before

        What a test suite isolates each test with (`runtime.testing.fresh_context`)."""
        values, parent = ROOT.values, ROOT.parent
        ROOT.values = Values(values.current)
        ROOT.parent = None
        try:
            yield
        finally:
            ROOT.values, ROOT.parent = values, parent

    def collecting(self) -> AbstractContextManager[None]:
        """Remember, for the block, what every `bus.context(...)` scope closing inside it said —
        the block an execution's signals are recorded in."""
        return CLOSED_SCOPES.collecting()

    def closed(self) -> dict[str, Any]:
        """What the scopes closed inside the execution in progress said — empty outside one.

        with context_scopes.collecting():
            with billing.context({"company": 3}): ...
            context_scopes.closed()   ->  {"company": 3}
        """
        return CLOSED_SCOPES.collected()


context_scopes = ContextScopes()
"""The one `ContextScopes` of the process — what the guards, the hooks and the signals use."""
