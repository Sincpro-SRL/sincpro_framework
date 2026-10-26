"""The context as a bus holds it: `bus.context(...)`, `self.context`, `bus.current_context()`.

    with billing.context({"tenant_id": "acme", "user_id": "ana"}):     a scope for the block
        billing(CommandIssueInvoice(...))                              reads tenant_id, user_id
    with billing.context({"lang": "es"}, global_scope=True):          every execution of billing

**Entering a bus is entering its node.** The first time a call reaches a bus, the bus's own node
(`ContextLevel.BUS` — what it publishes for every execution of its own) goes under the node in play, and the
call's scope under it: an entrypoint when nothing opened the flow, a scope otherwise. A nested call to
the same bus, or one inside its `bus.context(...)`, joins the scope already open.

**What `global_scope=True` writes is the bus's node**: every execution of this bus reads it live —
running ones included — and the block's end puts back what was there.

A context never needs to be saved and loaded by hand to reach another process: whatever a use
case hands on — a published event, a message to a queue, a call to another service, an answer
to the frontend — carries it, and the entrance on the other side opens its flow with it.
"""

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal

from sincpro_framework.context.domain.level import EntrypointKind
from sincpro_framework.context.domain.node import ContextNode
from sincpro_framework.context.infrastructure.closed_scopes import CLOSED_SCOPES

if TYPE_CHECKING:
    from sincpro_framework.use_bus import UseFramework


class FrameworkContext:
    """`bus.context(...)`: a scope of the bus for the block — or, with `global_scope`, what the
    bus publishes for every execution of its own."""

    def __init__(
        self,
        framework_instance: "UseFramework",
        context: Mapping[Any, Any] | None = None,
        global_scope: bool = False,
        kind: EntrypointKind | None = None,
    ) -> None:
        """Hold what the block opens with — a copy of `context` — nothing entered yet: the
        scope opens at `__enter__`, so one `bus.context(...)` built ahead of the `with` costs
        nothing.

            billing.context({"tenant_id": "acme"})
                ->  FrameworkContext(context={"tenant_id": "acme"}, global_scope=False)
            billing.context(None, global_scope=True)   ->  context {}, the bus's own node

        The mapping is copied: changing it after the call changes nothing in the block."""
        self._is_entered: bool = False
        self.framework = framework_instance
        self.context: dict[Any, Any] = dict(context or {})
        self.global_scope = global_scope
        self.kind = kind
        self._node: ContextNode | None = None
        self._block: Any = None
        self._published_before: Mapping[Any, Any] | None = None
        self._published_keys: list[Any] = []

    def __enter__(self) -> "UseFramework":
        """Open the block's context and answer the bus to call inside it.

        Context: a `with` block is the one place a caller says what its executions run with;
        the bus answered is the same bus, so `with billing.context(...) as bus: bus(dto)` and
        `billing(dto)` inside the block are one thing.

        1. Entered twice is a mistake: `RuntimeError`.
        2. `global_scope`: the bus's own node takes the values, what it held of those keys set
           aside for the end.
        3. Otherwise a scope of the bus opens with the values — an entrypoint of `kind` when
           no flow is open.
        Final: the bus.
        """
        if self._is_entered:
            raise RuntimeError("Context manager is already entered")
        self._is_entered = True
        framework = self.framework
        if self.global_scope:
            published = framework._coerced(self.context)
            self._published_before = framework._published.current
            self._published_keys = list(published)
            framework._published.update(published)
        else:
            self._block = framework._scope(framework._coerced(self.context), self.kind)
            self._node = self._block.__enter__()
        framework.logger.debug(f"with context: {sorted(map(str, self.context))}")
        return framework

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> Literal[False]:
        """Close the block's context and let any exception through.

        1. `global_scope`: the bus's node says again what it said of the block's keys before
           it — only those: what another write, or another pod of the bounded context, set
           meanwhile stays.
        2. Otherwise the scope's values are recorded for the execution it was opened in —
           its span, metrics and error close after it — and the scope is left.
        Final: `False`, so an exception raised in the block is never swallowed.
        """
        if self.global_scope:
            before = self._published_before
            if before is not None:
                published = self.framework._published
                published.update(
                    {key: before[key] for key in self._published_keys if key in before}
                )
                for key in self._published_keys:
                    if key not in before:
                        published.unset(key)
        elif self._block is not None and self._node is not None:
            # What the scope said still counts for the execution it was opened in: its span,
            # its metrics and its error close after the scope does (PRD_03 §4.10).
            CLOSED_SCOPES.record(self._node.own)
            self._block.__exit__(exc_type, exc_val, exc_tb)
        return False
