"""A FastAPI app's discovery document answered with its `ETag` — the fingerprint of its content
(`document_tag`) —, so a client that holds it is answered `304` (#200), as the REST and JSON-RPC
wires answer theirs.

    app.add_middleware(TaggedDocument, document=app.openapi, path="/openapi.json")
    GET /openapi.json                               ->  200, ETag: "5d41…"
    GET /openapi.json  If-None-Match: "5d41…"       ->  304, ETag: "5d41…"

Context: pure ASGI, ahead of FastAPI's own route — which answers no tag — and passing every other
request through untouched.
"""

from collections.abc import Callable, MutableMapping
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from sincpro_framework.entrypoints.adapters.fastapi.problems import Asgi, Receive, Send
from sincpro_framework.entrypoints.infrastructure.http import document_tag


class TaggedDocument:
    """Answers `GET <path>` with `document()` tagged by its content, `304` when the client holds
    that tag; anything else goes on to the app."""

    def __init__(self, app: Asgi, document: Callable[[], dict[str, Any]], path: str) -> None:
        """Wrap `app`; `document` builds what is served at `path`."""
        self.app = app
        self.document = document
        self.path = path

    async def __call__(
        self, scope: MutableMapping[str, Any], receive: Receive, send: Send
    ) -> None:
        """Serve one ASGI call.

        1. Anything but a `GET` of `path` — read below the app's root path — passes through.
        2. The document and its tag; the tag the client holds answered `304`.
        Final: the document, as JSON, with its `ETag`.
        """
        served = str(scope.get("path", "")).removeprefix(str(scope.get("root_path", "")))
        if scope["type"] != "http" or scope.get("method") != "GET" or served != self.path:
            await self.app(scope, receive, send)
            return
        written = self.document()
        tag = document_tag(written)
        if Request(scope).headers.get("if-none-match") == tag:
            response: Response = Response(status_code=304, headers={"ETag": tag})
        else:
            response = JSONResponse(written, headers={"ETag": tag})
        await response(scope, receive, send)
