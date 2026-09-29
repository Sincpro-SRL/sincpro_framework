"""JSON-RPC gateway: the declared surface of one or more UseFramework instances as JSON-RPC 2.0
methods named `{namespace}.{operation}` (PRD_15 §4) — the naming and `dispatch` are `rpc.wire`.

`.app()` is the batteries-included path: one process, this gateway's two routes, nothing
else. `.routes()` is the composable one — a plain list of Starlette `Route` objects with no
opinion about the app around them. A process that mounts several gateways under one ASGI app,
adds its own health check, CORS policy or auth middleware, or wires a `lifespan`, takes
`.routes()` and builds that `Starlette(...)` itself; `entrypoint_rpc` never reaches for
FastAPI, an ASGI framework, or a specific auth library on its own — that composition is the
host process's call, not this module's.
"""

import asyncio
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from sincpro_framework.auth.domain import Credentials
from sincpro_framework.auth.transports import challenges_of, credentials_from_asgi
from sincpro_framework.entrypoints.exposure import Exposure, Resolved, RpcBinding, Wire
from sincpro_framework.entrypoints.gateway import DEFAULT_LAYERS, Buses, Gateway
from sincpro_framework.entrypoints.rpc.jrpc import (
    DEFAULT_MAX_BATCH_SIZE,
    DEFAULT_MAX_BODY_BYTES,
    RESERVED_PREFIX,
    body_too_large,
    merge_http_context,
    unsupported_media_type,
)
from sincpro_framework.entrypoints.rpc.wire import (
    JsonRpcWire,
    Reply,
    RpcSurface,
    dispatch,
    http_status,
)
from sincpro_framework.use_bus import UseFramework

__all__ = ["RpcGateway", "build_rpc_app", "is_json_media_type", "merge_http_context"]

RPC_MISSING = (
    "Starlette/uvicorn is not installed. Install with: pip install sincpro-framework[rpc]"
)
ALIAS_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
JSON_MEDIA_TYPES = frozenset({"application/json", "application/json-rpc"})
"""What a request body may be sent as — `application/json-rpc` is what some JSON-RPC clients
send; a parameter such as `charset=utf-8` is allowed."""


def validate_alias(alias: str) -> str:
    """Context: an alias is the namespace of its methods by default, so `rpc` would publish
    `rpc.issue_invoice` under the prefix JSON-RPC 2.0 reserves for the protocol itself."""
    if not ALIAS_PATTERN.match(alias):
        raise ValueError(f"RPC instance alias [{alias}] must match {ALIAS_PATTERN.pattern}")
    if f"{alias}.".lower() == RESERVED_PREFIX:
        raise ValueError(
            f"RPC instance alias [{alias}] is reserved: JSON-RPC 2.0 keeps method names "
            f"beginning [{RESERVED_PREFIX}] for the protocol (only rpc.discover here)"
        )
    return alias


def is_json_media_type(content_type: str | None) -> bool:
    media_type = (content_type or "").split(";", 1)[0].strip().lower()
    return media_type in JSON_MEDIA_TYPES


class RpcGateway(Gateway):
    """JSON-RPC 2.0 over the declared surface of one or more buses (PRD_14, PRD_15 §4).

    Context: in `Exposure.DECLARED` (the default) a method is a use case bound with `@rpc()`
    or `bind`; `Exposure.CATALOG` publishes every one under the same names. The wire (`port`,
    a `JsonRpcWire`) names each `{namespace}.{operation}` and carries the document's title and
    version and the limits: `max_batch_size` bounds how many calls one batch runs (a longer one
    is answered a single Invalid Request, and nothing runs); `max_body_bytes` how much of a body
    is read before answering 413. Given a `port`, those are the port's to say.
    """

    wire = "rpc"

    def __init__(
        self,
        instances: Buses | None = None,
        layers: Iterable[str] = DEFAULT_LAYERS,
        title: str | None = None,
        version: str | None = None,
        max_batch_size: int | None = None,
        max_body_bytes: int | None = None,
        *,
        exposure: Exposure = Exposure.DECLARED,
        unguarded: bool = False,
        port: Wire[Any] | None = None,
    ):
        settings = (title, version, max_batch_size, max_body_bytes)
        if port is None:
            port = JsonRpcWire(
                title or "sincpro-rpc",
                version or "1.0.0",
                DEFAULT_MAX_BATCH_SIZE if max_batch_size is None else max_batch_size,
                DEFAULT_MAX_BODY_BYTES if max_body_bytes is None else max_body_bytes,
            )
        elif not isinstance(port, JsonRpcWire):
            raise TypeError(
                f"RpcGateway(port={type(port).__name__}): the port of a JSON-RPC gateway is a "
                "JsonRpcWire — subclass it to change how it derives"
            )
        elif any(one is not None for one in settings):
            raise ValueError(
                "RpcGateway(port=...): title, version and the limits are the port's — give "
                "them to JsonRpcWire(...)"
            )
        self._wire: JsonRpcWire = port
        self._built: tuple[object, RpcSurface] | None = None
        super().__init__(
            instances,
            layers,
            port.title,
            port.version,
            exposure=exposure,
            unguarded=unguarded,
            port=port,
        )

    @property
    def max_batch_size(self) -> int:
        return self._wire.max_batch_size

    @property
    def max_body_bytes(self) -> int:
        return self._wire.max_body_bytes

    def validate_alias(self, alias: str) -> str:
        return validate_alias(alias)

    def alias_for(self, framework_instance: UseFramework) -> str:
        return re.sub(r"[^A-Za-z0-9_-]", "-", framework_instance.name)

    def build(self) -> RpcSurface:
        """The validated surface as the wire serves it — built once per resolution, refused
        with `ExposureRefused` (every reason listed) when it does not hold."""
        surface: Sequence[Resolved[RpcBinding]] = self.surface()
        resolved = self._resolved
        if self._built is None or self._built[0] is not resolved:
            self._built = (resolved, self._wire.build(surface))
        return self._built[1]

    def methods(self) -> Mapping[str, Resolved[RpcBinding]]:
        """Each published method by its name."""
        return self.build().methods

    def discover(self) -> dict[str, Any]:
        return self.build().document

    def handle(
        self,
        payload: Any,
        context: Mapping[str, Any] | None = None,
        credentials: Credentials | None = None,
    ) -> Reply:
        """In process, no HTTP — `dispatch` over this gateway's surface.

        Context: `credentials` are authenticated by the `AccessControl` of each method's bus
        — none handed, and each runs as whoever the host already opened."""
        return dispatch(self.build(), payload, credentials=credentials, context=context)

    def challenges(self) -> list[str]:
        """The `WWW-Authenticate` values a 401 of this gateway answers with."""
        return challenges_of(
            catalog.framework_instance for catalog in self._catalogs.values()
        )

    def health_route(self, path: str = "/healthz") -> Any:
        """A `GET` route answering `200 {"status": "ok"}` / `503 {"status": "unhealthy"}`
        from `is_healthy()` — for a mesh, k8s probe, or load balancer that speaks
        plain HTTP, not JSON-RPC. Included in `.routes()`/`.app()` by default;
        `health_path=None` on either drops it for a host that wires its own.
        """
        try:
            from starlette.requests import Request  # pyright: ignore[reportMissingImports]
            from starlette.responses import (  # pyright: ignore[reportMissingImports]
                JSONResponse,
            )
            from starlette.routing import Route  # pyright: ignore[reportMissingImports]
        except ImportError as error:
            raise ImportError(RPC_MISSING) from error

        gateway = self

        async def healthz(_request: Request) -> JSONResponse:
            healthy = gateway.is_healthy()
            return JSONResponse(
                {"status": "ok" if healthy else "unhealthy"},
                status_code=200 if healthy else 503,
            )

        return Route(path, healthz, methods=["GET"])

    def routes(
        self,
        rpc_path: str = "/rpc",
        discover_path: str = "/openrpc.json",
        health_path: str | None = "/healthz",
    ) -> list[Any]:
        """This gateway's Starlette `Route`s, mountable into any app: JSON-RPC,
        OpenRPC discovery and — by default — a health check.

        The composable primitive: `Starlette(routes=[*gateway.routes(), *my_own_routes])`,
        or `Mount("/payments", routes=gateway.routes())` to namespace one gateway among
        several, or hand the list to a host that adds its own CORS/auth middleware and
        `lifespan` around it. `.app()` is this same list with no host around it supplied.
        `health_path=None` drops the health route for a host that wires its own.
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

        surface = self.build()

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
            status, headers = http_status(surface, reply)
            if reply is None:
                return Response(status_code=status, headers=headers)
            return JSONResponse(reply, status_code=status, headers=headers or None)

        async def rpc_endpoint(request: Request) -> Response:
            if not is_json_media_type(request.headers.get("content-type")):
                return answered(unsupported_media_type(sorted(JSON_MEDIA_TYPES)))
            raw = await bounded_body(request)
            if raw is None:
                return answered(body_too_large(surface.max_body_bytes))
            credentials = credentials_from_asgi(request.scope)
            reply = await asyncio.to_thread(
                dispatch, surface, raw, credentials=credentials, headers=request.headers
            )
            return answered(reply)

        async def openrpc_endpoint(_request: Request) -> JSONResponse:
            return JSONResponse(surface.document)

        routes = [
            Route(rpc_path, rpc_endpoint, methods=["POST"]),
            Route(discover_path, openrpc_endpoint, methods=["GET"]),
        ]
        if health_path is not None:
            routes.append(self.health_route(health_path))
        return routes

    def app(
        self,
        middleware: Sequence[Any] | None = None,
        routes: Sequence[Any] | None = None,
        lifespan: Any | None = None,
        health_path: str | None = "/healthz",
        **starlette_kwargs: Any,
    ) -> Any:
        """A standalone ASGI app: `POST /rpc` (JSON-RPC 2.0), `GET /openrpc.json`,
        and — by default — `GET /healthz`.

        The convenience path, for a process that serves only this gateway. `middleware` is a
        list of Starlette `Middleware(...)` instances (CORS, an auth check, request logging);
        `routes` are extra routes served alongside this gateway's own (another gateway's
        `.routes()`, a metrics endpoint); `lifespan` is a Starlette lifespan context manager
        for startup/shutdown (opening a connection pool, warming a cache); `health_path=None`
        drops the health route. Anything else `starlette.applications.Starlette` accepts
        passes through in `starlette_kwargs`.

        A process that wants a different CORS policy per set of buses composes one `Starlette`
        of its own instead: one `RpcGateway` (with its own `middleware=`) per trust domain,
        `Mount`ed at a different path or served on a different port — CORS is a property of
        the origin/port a browser talks to, not of which bus a JSON-RPC `method` happens to
        name inside the body.
        """
        try:
            from starlette.applications import Starlette  # pyright: ignore
        except ImportError as error:
            raise ImportError(RPC_MISSING) from error

        return Starlette(
            routes=[*self.routes(health_path=health_path), *(routes or ())],
            middleware=list(middleware) if middleware else None,
            lifespan=lifespan,
            **starlette_kwargs,
        )

    def run(self, host: str = "127.0.0.1", port: int = 8080, **kwargs: Any) -> None:
        """Start uvicorn on the JSON-RPC ASGI app. Extra kwargs go to uvicorn.run.

        For CORS, extra routes, a lifespan, or mounting several gateways in one process, build
        the app with `.app(middleware=..., routes=..., lifespan=...)` (or `.routes()` into a
        Starlette/app of your own) and pass that to `uvicorn.run(...)` directly instead of
        calling `.run()`.
        """
        try:
            import uvicorn  # pyright: ignore[reportMissingImports]
        except ImportError as error:
            raise ImportError(RPC_MISSING) from error
        uvicorn.run(self.app(), host=host, port=port, **kwargs)


def build_rpc_app(
    instances: Mapping[str, UseFramework],
    layers: Iterable[str] = DEFAULT_LAYERS,
    title: str | None = None,
    version: str | None = None,
    max_batch_size: int | None = None,
    max_body_bytes: int | None = None,
    *,
    exposure: Exposure = Exposure.DECLARED,
    unguarded: bool = False,
) -> Any:
    return RpcGateway(
        instances,
        layers=layers,
        title=title,
        version=version,
        max_batch_size=max_batch_size,
        max_body_bytes=max_body_bytes,
        exposure=exposure,
        unguarded=unguarded,
    ).app()
