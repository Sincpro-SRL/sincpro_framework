"""What the HTTP entrypoints read off a request's headers — the context the caller carried —
and how a discovery document is tagged for a client to revalidate it."""

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from sincpro_framework.context import context_propagation


def merge_http_context(headers: Mapping[str, str]) -> dict[str, Any]:
    """Fold transport headers into the framework context without touching DTO params.

        merge_http_context({"X-Correlation-Id": "c-1", "Baggage": "tenant_id=acme",
                            "traceparent": "00-4bf9...-01"})
        ->  {"tenant_id": "acme", "correlation_id": "c-1",
             "carrier": {"traceparent": "00-4bf9...-01"}}

    1. The context the caller carried — `baggage`, `sincpro-context` (what a frontend sends
       back), the identity headers (body context, merged later, still wins).
    2. carrier.traceparent from the W3C header, for OTel parent adoption.
    3. Final: a context dict handle_payload treats as inherited; empty becomes {}.
    """
    lowered = {key.lower(): value for key, value in headers.items()}
    merged: dict[str, Any] = context_propagation.extract(lowered)
    traceparent = lowered.get("traceparent")
    if traceparent:
        merged["carrier"] = {"traceparent": traceparent}
    return merged


def document_tag(document: Mapping[str, Any]) -> str:
    """The `ETag` of a discovery document: the fingerprint of its content, so every replica tags
    the same document alike with nothing shared, and a client holding it is answered `304`.

        document_tag({"openapi": "3.1.0", …})    ->  '"5d41402abc4b2a76…"'

    A model that changes changes its version (`x-sincpro.version`) and so the document's tag.
    """
    written = json.dumps(document, sort_keys=True, separators=(",", ":"), default=str)
    return f'"{hashlib.sha256(written.encode()).hexdigest()}"'
