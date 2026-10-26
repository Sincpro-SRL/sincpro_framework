"""The JSON-RPC surface served on Starlette — the `[rpc]` extra: its routes, a standalone app,
and uvicorn to run it.

    routes(surface, "/rpc", "/openrpc.json", health_route(is_healthy))
    ->  [Route("/rpc", POST), Route("/openrpc.json", GET), Route("/healthz", GET)]
    run(app(routes), "127.0.0.1", 8080)

Context: `.app()` is the batteries-included path: one process, the gateway's routes, nothing
else. `routes` is the composable one — a plain list of Starlette `Route` objects with no opinion
about the app around them. A process that mounts several gateways under one ASGI app, adds its
own health check, CORS policy or auth middleware, or wires a `lifespan`, takes the routes and
builds that `Starlette(...)` itself; nothing here reaches for FastAPI, an ASGI framework, or a
specific auth library on its own — that composition is the host process's call. Starlette and
uvicorn are imported only inside these functions.
"""

import asyncio
from collections.abc import Callable, Sequence
from typing import Any

from sincpro_framework.auth import Credentials
from sincpro_framework.entrypoints.adapters.rpc.jrpc import (
    body_too_large,
    unsupported_media_type,
)
from sincpro_framework.entrypoints.adapters.rpc.wire import (
    Reply,
    RpcSurface,
    dispatch,
    http_status,
)

RPC_MISSING = (
    "Starlette/uvicorn is not installed. Install with: pip install sincpro-framework[rpc]"
)
JSON_MEDIA_TYPES = frozenset({"application/json", "application/json-rpc"})
"""What a request body may be sent as — `application/json-rpc` is what some JSON-RPC clients
send; a parameter such as `charset=utf-8` is allowed."""


def is_json_media_type(content_type: str | None) -> bool:
    """Whether a `Content-Type` is one a JSON-RPC body may be sent as — its parameters
    ignored.

    is_json_media_type("application/json; charset=utf-8")  ->  True
    is_json_media_type("text/plain")                       ->  False
    """
    media_type = (content_type or "").split(";", 1)[0].strip().lower()
    return media_type in JSON_MEDIA_TYPES


def health_route(is_healthy: Callable[[], bool], path: str = "/healthz") -> Any:
    """A `GET` route answering `200 {"status": "ok"}` / `503 {"status": "unhealthy"}` from
    `is_healthy()` — for a mesh, k8s probe, or load balancer that speaks plain HTTP, not
    JSON-RPC.

        health_route(gateway.is_healthy)   ->  Route("/healthz", methods=["GET"])
        GET /healthz                       ->  200 {"status": "ok"}
        GET /healthz  <a bus torn down>    ->  503 {"status": "unhealthy"}

    1. Starlette imported — refused with the extra-install hint without it.
    2. An endpoint that asks `is_healthy()` on every request.
    Final: a `GET` route at `path` serving it.
    """
    try:
        from starlette.requests import Request  # pyright: ignore[reportMissingImports]
        from starlette.responses import (  # pyright: ignore[reportMissingImports]
            JSONResponse,
        )
        from starlette.routing import Route  # pyright: ignore[reportMissingImports]
    except ImportError as error:
        raise ImportError(RPC_MISSING) from error

    async def healthz(_request: Request) -> JSONResponse:
        """200 `{"status": "ok"}` while every bus is built, else 503 `unhealthy`."""
        healthy = is_healthy()
        return JSONResponse(
            {"status": "ok" if healthy else "unhealthy"},
            status_code=200 if healthy else 503,
        )

    return Route(path, healthz, methods=["GET"])


def routes(
    surface: RpcSurface,
    rpc_path: str = "/rpc",
    discover_path: str = "/openrpc.json",
    health: Any | None = None,
) -> list[Any]:
    """The Starlette `Route`s serving `surface`: JSON-RPC, OpenRPC discovery and — when given —
    the `health` route.

        routes(surface)
        ->  [Route("/rpc", POST), Route("/openrpc.json", GET)]
        POST /rpc  {"jsonrpc": "2.0", "id": 1, "method": "billing.issue_invoice", ...}
        ->  200 {"jsonrpc": "2.0", "id": 1, "result": {...}}
        POST /rpc  Content-Type: text/plain     ->  415

    1. Starlette imported — refused with the extra-install hint without it.
    2. `POST rpc_path`, per request:
       2.1 a body not sent as JSON is refused (415) before it is read;
       2.2 the body read up to `max_body_bytes` — past it, 413;
       2.3 `dispatch` in a worker thread, with the request's credentials and headers;
       2.4 the reply answered with the status and headers `http_status` gives it.
    3. `GET discover_path`: the OpenRPC document of that surface.
    Final: the `health` route after them, when one is given.
    """
    try:
        from starlette.requests import Request  # pyright: ignore[reportMissingImports]
        from starlette.responses import (  # pyright: ignore[reportMissingImports]
            JSONResponse,
            Response,
        )
        from starlette.routing import Route  # pyright: ignore[reportMissingImports]
    except ImportError as error:
        raise ImportError(RPC_MISSING) from error

    async def bounded_body(request: Request) -> bytes | None:
        """The body, or None past `max_body_bytes` — refused by its `Content-Length`
        before a byte is read, and counted as it streams when it has none."""
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > surface.max_body_bytes:
            return None
        received = bytearray()
        async for chunk in request.stream():
            received.extend(chunk)
            if len(received) > surface.max_body_bytes:
                return None
        return bytes(received)

    def answered(reply: Reply) -> Response:
        """The reply as an HTTP response — `http_status` gives its status and headers; no
        reply is an empty one (204)."""
        status, headers = http_status(surface, reply)
        if reply is None:
            return Response(status_code=status, headers=headers)
        return JSONResponse(reply, status_code=status, headers=headers or None)

    async def rpc_endpoint(request: Request) -> Response:
        """`POST /rpc`: one JSON-RPC body answered.

        1. A body not sent as JSON is refused (415) before it is read.
        2. The body, bounded by `max_body_bytes` — past it, 413.
        3. `dispatch` in a worker thread, with the request's credentials and headers — the
           bus authenticates in the thread it runs in.
        Final: the reply, its status by `http_status`.
        """
        if not is_json_media_type(request.headers.get("content-type")):
            return answered(unsupported_media_type(sorted(JSON_MEDIA_TYPES)))
        raw = await bounded_body(request)
        if raw is None:
            return answered(body_too_large(surface.max_body_bytes))
        credentials = Credentials.from_asgi(request.scope)
        reply = await asyncio.to_thread(
            dispatch, surface, raw, credentials=credentials, headers=request.headers
        )
        return answered(reply)

    async def openrpc_endpoint(_request: Request) -> JSONResponse:
        """`GET /openrpc.json`: the document of the surface the routes were built from."""
        return JSONResponse(surface.document)

    served = [
        Route(rpc_path, rpc_endpoint, methods=["POST"]),
        Route(discover_path, openrpc_endpoint, methods=["GET"]),
    ]
    if health is not None:
        served.append(health)
    return served


def app(
    served: Sequence[Any],
    middleware: Sequence[Any] | None = None,
    lifespan: Any | None = None,
    **starlette_kwargs: Any,
) -> Any:
    """A standalone Starlette app of `served` — `middleware` (CORS, an auth check, request
    logging), a `lifespan`, and anything else Starlette takes."""
    try:
        from starlette.applications import Starlette  # pyright: ignore
    except ImportError as error:
        raise ImportError(RPC_MISSING) from error

    return Starlette(
        routes=list(served),
        middleware=list(middleware) if middleware else None,
        lifespan=lifespan,
        **starlette_kwargs,
    )


def run(asgi_app: Any, host: str = "127.0.0.1", port: int = 8080, **kwargs: Any) -> None:
    """Serve `asgi_app` with uvicorn until stopped. Extra kwargs go to `uvicorn.run`."""
    try:
        import uvicorn  # pyright: ignore[reportMissingImports]
    except ImportError as error:
        raise ImportError(RPC_MISSING) from error
    uvicorn.run(asgi_app, host=host, port=port, **kwargs)
