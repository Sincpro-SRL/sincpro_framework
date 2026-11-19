"""What the HTTP entrypoints read off a request's headers — the context the caller carried —,
how a discovery document is tagged for a client to revalidate it, and the files a record holds
served by their links."""

import hashlib
import json
import unicodedata
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from sincpro_framework.context import context_propagation
from sincpro_framework.ddd.stored_file import linking

if TYPE_CHECKING:
    from sincpro_framework.data_layer.object_storage import ObjectStorage


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


def _problem(status: int, title: str, detail: str) -> Any:
    """An RFC 9457 problem, as every refusal of these routes is answered."""
    from starlette.responses import JSONResponse

    return JSONResponse(
        {"type": "about:blank", "title": title, "status": status, "detail": detail},
        status_code=status,
        media_type="application/problem+json",
    )


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
    from starlette.responses import Response
    from starlette.routing import Route

    async def served(request: Any) -> Any:
        """The contract of the alias the path names."""
        alias = request.path_params["alias"]
        found = contract(alias)
        if found is None:
            return _problem(404, "Not Found", f"no client contract for {alias!r}")
        body, version = found
        tag = f'"{version}"'
        if request.headers.get("if-none-match") == tag:
            return Response(status_code=304, headers={"ETag": tag})
        return Response(body, media_type="application/json", headers={"ETag": tag})

    return Route(f"{path.rstrip('/')}/{{alias}}.json", served, methods=["GET"])


def disposition(kind: str, name: str) -> str:
    """A `Content-Disposition` that keeps a file's name in any language: in ASCII, its accents
    dropped, for old clients, and whole in UTF-8 (RFC 6266) for the rest.

        disposition("attachment", "Órgano ñ.jpg")
        ->  attachment; filename="Organo n.jpg"; filename*=UTF-8''%C3%93rgano%20%C3%B1.jpg
    """
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    plain = plain.replace('"', "").replace("\\", "")
    return f"{kind}; filename=\"{plain or 'file'}\"; filename*=UTF-8''{quote(name)}"


def files_route(path: str, storage: "ObjectStorage") -> Any:
    """`GET {path}/{token}` as a Starlette route: the object a link names (`ObjectStorage.link`),
    to see it — or to save it, with `?download`.

        GET /files/cGhvdG9z….1763600400.Zk3…             ->  200, image/jpeg, inline; filename*=…
        GET /files/cGhvdG9z….1763600400.Zk3…?download    ->  200, attachment; filename*=…
        GET /files/<a link that ended>                   ->  410, a problem
        GET /files/<a link not made here>                ->  404, a problem

    1. The link opened (`linked`): ended is a 410, forged a 404 — nothing else is told.
    2. A store that serves its bytes itself (`direct`) is a redirect to it.
    3. Else the bytes, a part at a time, with their type, their size, the name they were sent
       with, and a private cache until the link ends — the same link is the same object.
    Final: an object gone from the store is a 404.
    """
    from starlette.responses import RedirectResponse, StreamingResponse
    from starlette.routing import Route

    from sincpro_framework.data_layer.object_storage import (
        ObjectNotFound,
        PublicationRefused,
        Refusal,
    )

    async def served(request: Any) -> Any:
        """The object the path's link names."""
        try:
            linked = storage.linked(request.path_params["token"])
        except PublicationRefused as refused:
            if refused.reason is Refusal.EXPIRED:
                return _problem(410, "Gone", "the link has ended: read the record again")
            return _problem(404, "Not Found", "no file at this link")
        url = storage.direct(linked.key)
        if url:
            return RedirectResponse(url, status_code=307)
        try:
            streamed = storage.stream(linked.key)
        except ObjectNotFound:
            return _problem(404, "Not Found", "the file is no longer stored")
        ref = streamed.ref
        name = ref.metadata.get("filename") or ref.key.rsplit("/", 1)[-1]
        kind = "attachment" if "download" in request.query_params else "inline"
        left = max(0, int((linked.ends_at - datetime.now(UTC)).total_seconds()))
        return StreamingResponse(
            streamed.chunks(),
            media_type=ref.content_type,
            headers={
                "Content-Length": str(ref.size),
                "Content-Disposition": disposition(kind, name),
                "Cache-Control": f"private, max-age={left}",
            },
        )

    return Route(f"{path.rstrip('/')}/{{token}}", served, methods=["GET"])


class FileLinks:
    """An ASGI middleware: inside each HTTP request, every record answer writes each
    `StoredFile` and `Rendition` with its link — `{root_path}{path}/{token}`, the route `files_route` serves.

        app.add_middleware(FileLinks, path="/files", storage=storage)
        GET /v1/organs/7    ->  {"photo": {"name": "directorio.jpg", …, "url": "/files/cGhv…"}}

    Context: the link is the answer's — whoever may read the record may see its file — and is
    made while the answer is written, after the use case ran; `linking` carries it there.
    """

    def __init__(self, app: Any, path: str, storage: "ObjectStorage") -> None:
        """`app` the ASGI app it wraps; `path` where `files_route` is mounted; `storage` whose
        secret signs the links."""
        self.app = app
        self.path = path.rstrip("/")
        self.storage = storage

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        base = f"{scope.get('root_path', '')}{self.path}"

        def link(key: str) -> str:
            return f"{base}/{self.storage.link(key)}"

        with linking(link):
            await self.app(scope, receive, send)
