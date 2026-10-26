"""The context handed on to whatever receives it over text headers — HTTP, gRPC metadata, a
broker's message.

    headers = inject(use_context())         what a message or a request carries

Context: handing the context on is this package's operation — the execution in play becomes the
cause (`context_executions.handed_on`); writing it on headers is the transport's
(`adapters.propagation.headers`).
"""

from collections.abc import Mapping
from typing import Any

from sincpro_framework.context.adapters.propagation import headers
from sincpro_framework.context.services.executions import handed_on


def inject(context: Mapping[Any, Any]) -> dict[str, str]:
    """The headers that carry `context` — caused by the execution in play, in its flow.

    1. The text keys of `context`, handed on: the execution in play becomes the cause, its
       own `execution_id` stays here, the node in play is named when a store shares it.
    2. Final: written as headers — the identity and the node on their own, every travelling
       key as `sincpro-context`, the travelling scalars as `baggage` (`headers`).

        inject({"tenant_id": "acme"})      (inside execution E1 of flow C)
        ->  {"x-correlation-id": "C", "x-causation-id": "E1",
             "sincpro-context": '{"tenant_id":"acme","causation_id":"E1",…}',
             "baggage": "tenant_id=acme,causation_id=E1,…"}
    """
    return headers(
        handed_on({key: value for key, value in context.items() if isinstance(key, str)})
    )
