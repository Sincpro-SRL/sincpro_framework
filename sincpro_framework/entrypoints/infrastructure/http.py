"""What the HTTP entrypoints read off a request's headers — the context the caller carried —
and how a discovery document is tagged for a client to revalidate it."""

import hashlib
import json
from collections.abc import Callable, Mapping
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


def contract_route(path: str, contract: Callable[[str], tuple[bytes, str] | None]) -> Any:
    """`GET {path}/{alias}.json` as a Starlette route: the client contract of the bounded context
    a gateway publishes as `alias` — the bytes its export writes —, tagged by its version, `304`
    to a client that holds it.

        contract_route("/client-contract", partial(contract_of, gateway))
        GET /client-contract/billing.json                          ->  200, ETag: "9b1c…"
        GET /client-contract/billing.json  If-None-Match: "9b1c…"  ->  304
        GET /client-contract/nowhere.json                          ->  404, a problem

    1. `contract(alias)` per request — so a use case registered while it runs, or a model an
       extension changed, is served at once —; `None`, an alias it does not serve, is a 404.
    2. The tag the client holds answered `304`.
    Final: the bytes, as JSON, with their `ETag`.
    """
    # Imported when asked: an HTTP gateway is an extra; this module is read by every entrypoint.
    from starlette.responses import JSONResponse, Response
    from starlette.routing import Route

    async def served(request: Any) -> Any:
        """The contract of the alias the path names."""
        alias = request.path_params["alias"]
        found = contract(alias)
        if found is None:
            return JSONResponse(
                {
                    "type": "about:blank",
                    "title": "Not Found",
                    "status": 404,
                    "detail": f"no client contract for {alias!r}",
                },
                status_code=404,
                media_type="application/problem+json",
            )
        body, version = found
        tag = f'"{version}"'
        if request.headers.get("if-none-match") == tag:
            return Response(status_code=304, headers={"ETag": tag})
        return Response(body, media_type="application/json", headers={"ETag": tag})

    return Route(f"{path.rstrip('/')}/{{alias}}.json", served, methods=["GET"])
