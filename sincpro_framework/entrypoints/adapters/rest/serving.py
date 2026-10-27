"""The REST route table served on Starlette — the `[rest]` extra: each route's endpoint, the
OpenAPI document, its docs page, a health check, a standalone app and uvicorn to run it.

    routes(rest_routes, challenges, document, is_healthy, "sincpro-rest")
    ->  [Route("/billing/command-issue-invoice", methods=["POST"]), ...,
         Route("/openapi.json"), Route("/docs"), Route("/healthz")]
    run(app(routes), "127.0.0.1", 8080)

Context: the route table and the document are built without a web library (`routing`,
`openapi`); Starlette is only what serves them, imported inside these functions.
"""

import asyncio
import json
import math
from collections.abc import Callable, Sequence
from typing import Any

from sincpro_framework.auth import Credentials
from sincpro_framework.entrypoints.adapters.rest import exceptions
from sincpro_framework.entrypoints.adapters.rest.routing import (
    RestRoute,
    call_route,
    failure_answer,
    payload_from_query,
)
from sincpro_framework.entrypoints.infrastructure.http import merge_http_context

REST_MISSING = (
    "Starlette/uvicorn is not installed. Install with: pip install sincpro-framework[rest]"
)
DOCS_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>{title}</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css"></head>
<body><div id="ui"></div>
<script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
<script>SwaggerUIBundle({{url: "{openapi}", dom_id: "#ui"}});</script></body></html>"""


async def payload_of(request: Any, route: RestRoute) -> dict[str, Any]:
    """The payload a request carries for `route`, before any validation — the DTO's.

        GET  /billing/invoices/F-9?status=open
        ->  {"status": "open", "invoice_id": "F-9"}
        POST /billing/command-issue-invoice  {"total": 10}  ->  {"total": 10}

    1. `GET`: the query string, each non-plain field decoded from JSON.
    2. Any other method: the body, a JSON object — or `InvalidRequest` (400).
    Final: the path's fields over either.
    """
    if request.method == "GET":
        payload = payload_from_query(dict(request.query_params), route.operation.json_schema)
    else:
        raw = await request.body()
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise exceptions.InvalidRequest(f"the body is not JSON: {error}") from error
        if not isinstance(payload, dict):
            raise exceptions.InvalidRequest("the body is a JSON object of the DTO's fields")
    return {**payload, **request.path_params}


def routes(
    rest_routes: Sequence[RestRoute],
    challenges: Callable[[], list[str]],
    document: Callable[[], dict[str, Any]],
    is_healthy: Callable[[], bool],
    title: str,
    openapi_path: str | None = "/openapi.json",
    docs_path: str | None = "/docs",
    health_path: str | None = "/healthz",
) -> list[Any]:
    """The Starlette routes of `rest_routes`, mountable into any app — every use case, and,
    unless `None`, the OpenAPI document, its docs page and a health check.

        routes(rest_routes, challenges, document, is_healthy, "sincpro-rest")
        ->  [Route("/billing/command-issue-invoice", methods=["POST"]), ...,
             Route("/openapi.json"), Route("/docs"), Route("/healthz")]
        routes(..., openapi_path=None, health_path=None)  ->  the use cases only

    1. Starlette imported — refused with the extra-install hint without it.
    2. One `Route` per REST route, named by its `operationId`, whose endpoint:
       2.1 reads the payload (`payload_of`) — an unreadable one answered 400;
       2.2 merges the context the caller carried (`merge_http_context`);
       2.3 runs `call_route` in a worker thread, with the request's credentials;
       2.4 answers its `(status, body)` as JSON — a 401 with the `challenges`.
    3. The OpenAPI `document` at `openapi_path`, built per request.
    4. The Swagger UI page at `docs_path` — only when there is a document to read.
    Final: the health check at `health_path` — 200 `ok`, or 503 `unhealthy`.
    """
    try:
        from starlette.requests import Request  # pyright: ignore[reportMissingImports]
        from starlette.responses import (  # pyright: ignore[reportMissingImports]
            HTMLResponse,
            JSONResponse,
        )
        from starlette.routing import Route  # pyright: ignore[reportMissingImports]
    except ImportError as error:
        raise ImportError(REST_MISSING) from error

    def endpoint(route: RestRoute) -> Any:
        """The Starlette endpoint of one route."""

        async def answer(request: Request) -> JSONResponse:
            """Answer one request on this route.

            1. The payload out of the request — a 400 when it cannot be read.
            2. The context the caller carried (`merge_http_context`).
            3. `call_route` in a worker thread, with the request's credentials.
            Final: its `(status, body)` as JSON — a 401 with the buses' challenges in
            `WWW-Authenticate`, a failure that says how long to wait with `Retry-After`.
            """
            try:
                payload = await payload_of(request, route)
            except exceptions.InvalidRequest as error:
                status, body = failure_answer(error)
                return JSONResponse({"error": body}, status_code=status)
            context = merge_http_context(request.headers) or None
            status, body = await asyncio.to_thread(
                call_route,
                route,
                payload,
                context,
                Credentials.from_asgi(request.scope),
            )
            headers: dict[str, str] = {}
            if status == 401 and (refusals := challenges()):
                headers["www-authenticate"] = ", ".join(refusals)
            wait = body.get("error", {}).get("retry_after") if status >= 400 else None
            if wait is not None:
                headers["retry-after"] = str(math.ceil(wait))
            return JSONResponse(body, status_code=status, headers=headers or None)

        return answer

    served = [
        Route(
            route.path,
            endpoint(route),
            methods=list(route.methods),
            name=route.operation_id,
        )
        for route in rest_routes
    ]
    if openapi_path is not None:

        async def openapi(_request: Request) -> JSONResponse:
            """The OpenAPI document, built per request — it follows the catalog."""
            return JSONResponse(document())

        served.append(Route(openapi_path, openapi, methods=["GET"]))
    if docs_path is not None and openapi_path is not None:
        page = DOCS_PAGE.format(title=title, openapi=openapi_path)

        async def docs(_request: Request) -> HTMLResponse:
            """A Swagger UI page reading `openapi_path`."""
            return HTMLResponse(page)

        served.append(Route(docs_path, docs, methods=["GET"]))
    if health_path is not None:

        async def health(_request: Request) -> JSONResponse:
            """200 `{"status": "ok"}` while every bus is built, else 503 `unhealthy`."""
            healthy = is_healthy()
            return JSONResponse(
                {"status": "ok" if healthy else "unhealthy"},
                status_code=200 if healthy else 503,
            )

        served.append(Route(health_path, health, methods=["GET"]))
    return served


def app(
    served: Sequence[Any],
    middleware: Sequence[Any] | None = None,
    lifespan: Any | None = None,
    **starlette_kwargs: Any,
) -> Any:
    """A standalone Starlette app of `served` — `middleware` (CORS, `IdentityMiddleware`, a
    rate limit), a `lifespan`, and anything else Starlette takes."""
    try:
        from starlette.applications import Starlette  # pyright: ignore
    except ImportError as error:
        raise ImportError(REST_MISSING) from error
    return Starlette(
        routes=list(served),
        middleware=list(middleware) if middleware else None,
        lifespan=lifespan,
        **starlette_kwargs,
    )


def run(asgi_app: Any, host: str = "127.0.0.1", port: int = 8080, **kwargs: Any) -> None:
    """Serve `asgi_app` with uvicorn until stopped — a quick start; a deployment serves the app
    with its own server and settings. `kwargs` go to `uvicorn.run`."""
    try:
        import uvicorn  # pyright: ignore[reportMissingImports]
    except ImportError as error:
        raise ImportError(REST_MISSING) from error
    uvicorn.run(asgi_app, host=host, port=port, **kwargs)
