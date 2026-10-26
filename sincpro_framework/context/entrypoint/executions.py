"""`context_executions`: the execution in play as the rest of the framework reaches it —
opened by a bus, read from anywhere, handed on to whatever it causes.

    with context_executions.opened(dto, "billing", ContextLevel.FEATURE) as node:   one use case running
        context_executions.prepare(node, feature, providers)                        what it runs with
    context_executions.current()                     the execution in play, `None` outside one
    context_executions.handed_on(context)            what an execution elsewhere receives
    context_executions.chain_for(event)              what a new event lacks of its chain
    context_executions.chain_keys                    the context keys of an execution's identity

Context: a bus, a repository, a publisher or an entrance of another package never reaches into
how an execution is minted or where its node is held — it asks here.
"""

from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from typing import Any

from sincpro_framework.common.ids import new_entity_id
from sincpro_framework.context.domain.execution import CHAIN_KEYS, Execution
from sincpro_framework.context.domain.keys import ContextKey
from sincpro_framework.context.domain.level import ContextLevel
from sincpro_framework.context.domain.node import ContextNode
from sincpro_framework.context.domain.providers import ContextProvider
from sincpro_framework.context.services.executions import (
    chain_for,
    current_execution,
    handed_on,
    opened_execution,
)
from sincpro_framework.context.services.providers import prepare


class ContextExecutions:
    """The executions of the process: one opened per use case run, the one in play, and what it
    hands on — one object, `executions`, so nothing outside the context reaches into how the
    identity is minted."""

    @property
    def chain_keys(self) -> tuple[ContextKey, ...]:
        """The context keys of an execution's identity — the names a `DomainEvent` carries too.

        context_executions.chain_keys   ->  ("execution_id", "causation_id", "correlation_id")
        """
        return CHAIN_KEYS

    def opened(
        self,
        dto: Any,
        bus: str,
        level: ContextLevel,
        new_id: Callable[[], str] = new_entity_id,
        owner: Any = None,
    ) -> AbstractContextManager[ContextNode]:
        """The node of one execution of `dto` on `bus`, in play until the block ends — its
        identity minted by the one rule: continuing the chain of the event it runs, of the
        execution that called it, or of what its entrance was handed.

            with context_executions.opened(CreateInvoice(...), "billing", ContextLevel.FEATURE) as node:
                node.execution   ->  Execution(execution_id="01J9", causation_id="01J8", …)
        """
        return opened_execution(dto, bus, level, new_id, owner)

    def prepare(
        self, node: ContextNode, handler: object, providers: Sequence[ContextProvider]
    ) -> None:
        """The execution's node filled by the providers of its bus, and refused with
        `ContextRequired` when a key `handler` declared with `requires_context` is missing.

            context_executions.prepare(node, IssueInvoice(), [tenant_provider])   ->  tenant_id on node
        """
        prepare(node, handler, providers)

    def current(self) -> Execution | None:
        """The execution in play — from a Feature, an interceptor, a hook or a component beside
        the buses; `None` outside one."""
        return current_execution()

    def handed_on(self, context: Mapping[str, Any]) -> dict[str, Any]:
        """`context` as an execution elsewhere receives it — caused by the execution in play, in
        its flow; its own `execution_id` stays here.

            context_executions.handed_on({"tenant_id": "acme"})      # inside E1 of flow C
                ->  {"tenant_id": "acme", "causation_id": "E1", "correlation_id": "C"}
        """
        return handed_on(context)

    def chain_for(self, event: Any) -> dict[str, str]:
        """What a new event lacks of the execution producing it — `causation_id`,
        `correlation_id` — empty outside an execution or for an event already stored.

            context_executions.chain_for(InvoiceIssued(...))   ->  {"causation_id": "E1",
                                                            "correlation_id": "C"}
        """
        return chain_for(event)


context_executions = ContextExecutions()
"""The one `ContextExecutions` of the process — what the buses and every component beside them use."""
