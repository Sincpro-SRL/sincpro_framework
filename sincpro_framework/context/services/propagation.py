"""The context handed on to whatever receives it over text headers — HTTP, gRPC metadata, a
broker's message.

    headers = inject()                      what a message or a request carries: the flow in play

Context: handing the context on is this package's operation — the execution in play becomes the
cause (`context_executions.handed_on`); writing it on headers is the transport's
(`adapters.propagation.headers`).
"""

from collections.abc import Mapping
from typing import Any

from sincpro_framework.context.adapters.propagation import headers
from sincpro_framework.context.infrastructure.tree import current
from sincpro_framework.context.services.executions import handed_on


def inject(context: Mapping[Any, Any] | None = None) -> dict[str, str]:
    """The headers that carry `context` — the flow in play when none is given — caused by the
    execution in play, in its flow.

    1. What crosses: `context` as given, or the flow in play (`ContextNode.flow`) — never the
       global, the process or a bounded context's level, which the other side reads its own.
    2. Its text keys handed on: the execution in play becomes the cause, its own
       `execution_id` stays here, the node in play is named when a store shares it.
    3. Final: written as headers — the identity and the node on their own, every travelling
       key as `sincpro-context`, the travelling scalars as `baggage` (`headers`).

        inject()                           (inside execution E1 of flow C, tenant acme)
        ->  {"x-correlation-id": "C", "x-causation-id": "E1",
             "sincpro-context": '{"tenant_id":"acme","causation_id":"E1",…}',
             "baggage": "tenant_id=acme,causation_id=E1,…"}
        inject({"tenant_id": "beta"})      ->  the same, saying exactly what was given
    """
    crossing = current().flow() if context is None else context
    return headers(
        handed_on({key: value for key, value in crossing.items() if isinstance(key, str)})
    )
