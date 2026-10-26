"""`context_propagation`: the context across a transport made of text headers, as the wires
and the brokers reach it.

    headers = context_propagation.inject(use_context())    what a message or a request carries
    context = context_propagation.extract(headers)         what an entrance opens its flow with
    context_propagation.identity_headers                   {"x-correlation-id": "correlation_id", …}
    context_propagation.trace_keys                         what travels in the trace's own headers

Context: an HTTP, gRPC or queue wire never reaches into which headers the context is written in —
it asks here.
"""

from collections.abc import Mapping
from typing import Any

from sincpro_framework.context.adapters.propagation import IDENTITY_HEADERS, extract
from sincpro_framework.context.domain.keys import PLUMBING
from sincpro_framework.context.services.propagation import inject


class ContextPropagation:
    """The context written on headers and read back — one object, `propagation`."""

    @property
    def identity_headers(self) -> dict[str, str]:
        """The headers an entrypoint reads the identity from, lowercase, by the context key
        each carries.

            context_propagation.identity_headers
                ->  {"x-correlation-id": "correlation_id", "x-causation-id": "causation_id",
                     "x-execution-id": "execution_id"}
        """
        return IDENTITY_HEADERS

    @property
    def trace_keys(self) -> frozenset[str]:
        """The keys that carry the trace between buses — it travels in its own W3C headers, so
        the context never carries them: `{"trace_id", "span_id", "carrier"}`."""
        return PLUMBING

    def inject(self, context: Mapping[Any, Any]) -> dict[str, str]:
        """The headers that carry `context` — caused by the execution in play, in its flow.

        context_propagation.inject({"tenant_id": "acme"})      # inside execution E1 of flow C
            ->  {"x-correlation-id": "C", "x-causation-id": "E1",
                 "sincpro-context": '{"tenant_id":"acme",…}', "baggage": "tenant_id=acme,…"}
        """
        return inject(context)

    def extract(self, headers: Mapping[str, Any]) -> dict[str, Any]:
        """The context a request or a message carried — header names read in any case.

        context_propagation.extract({"Baggage": "lang=es", "X-Correlation-Id": "C"})
            ->  {"lang": "es", "correlation_id": "C"}
        """
        return extract(headers)


context_propagation = ContextPropagation()
"""The one `ContextPropagation` of the process — what every wire and every broker uses."""
