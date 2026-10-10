"""What the HTTP entrypoints read off a request's headers: the context the caller carried."""

from collections.abc import Mapping
from typing import Any

from sincpro_framework.context.adapters.propagation import extract


def merge_http_context(headers: Mapping[str, str]) -> dict[str, Any]:
    """Fold transport headers into the framework context without touching DTO params.

    1. The context the caller carried — `baggage`, `sincpro-context` (what a frontend sends
       back), the identity headers (body context, merged later, still wins).
    2. carrier.traceparent from the W3C header, for OTel parent adoption.
    3. Final: a context dict handle_payload treats as inherited; empty becomes {}.
    """
    lowered = {key.lower(): value for key, value in headers.items()}
    merged: dict[str, Any] = extract(lowered)
    traceparent = lowered.get("traceparent")
    if traceparent:
        merged["carrier"] = {"traceparent": traceparent}
    return merged
