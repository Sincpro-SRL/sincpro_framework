"""JSON-RPC gateway: wrap one or more UseFramework instances as JSON-RPC 2.0 methods.

`.app()` is the batteries-included path: one process, this gateway's two routes, nothing
else. `.routes()` is the composable one — a plain list of Starlette `Route` objects with no
opinion about the app around them. A process that mounts several gateways under one ASGI app,
adds its own health check, CORS policy or auth middleware, or wires a `lifespan`, takes
`.routes()` and builds that `Starlette(...)` itself; `entrypoint_rpc` never reaches for
FastAPI, an ASGI framework, or a specific auth library on its own — that composition is the
host process's call, not this module's.
"""

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Self

from sincpro_framework.entrypoints.catalog import Catalog
from sincpro_framework.entrypoints.const import Layer, Wrapper
from sincpro_framework.entrypoints.rpc.jrpc import (
    PARSE_ERROR,
    MethodIndex,
    handle_payload,
    jsonrpc_error,
    method_name,
    method_object,
    openrpc_document,
)
from sincpro_framework.use_bus import UseFramework

RPC_MISSING = (
    "Starlette/uvicorn is not installed. Install with: pip install sincpro-framework[rpc]"
)
ALIAS_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
DEFAULT_LAYERS = (Layer.APP_SERVICES, Layer.FEATURES)


def validate_alias(alias: str) -> str:
    if not ALIAS_PATTERN.match(alias):
        raise ValueError(f"RPC instance alias [{alias}] must match {ALIAS_PATTERN.pattern}")
    return alias


def merge_http_context(headers: Mapping[str, str]) -> dict[str, Any]:
    """Fold transport headers into the framework context without touching DTO params.

    1. correlation_id from X-Correlation-Id (body context, merged later, still wins).
    2. carrier.traceparent from the W3C header, for OTel parent adoption.
    3. Final: a context dict handle_payload treats as inherited; empty becomes {}.
    """
    merged: dict[str, Any] = {}
    correlation = headers.get("x-correlation-id")
    if correlation:
        merged["correlation_id"] = correlation
    traceparent = headers.get("traceparent")
    if traceparent:
        merged["carrier"] = {"traceparent": traceparent}
    return merged


def index_methods(catalogs: Mapping[str, Catalog], layers: Iterable[str]) -> MethodIndex:
    """Index JSON-safe Features/ApplicationServices as instance.layer.DtoName."""
    allowed = set(layers)
    methods: MethodIndex = {}
    for alias, catalog in catalogs.items():
        for operation in catalog.get_scalar_use_cases(filter_binaries_schema=True):
            if operation.layer not in allowed:
                continue
            name = method_name(alias, operation.layer, operation.name)
            methods[name] = (alias, catalog.framework_instance, operation)
    return methods


class RpcGateway:
    """JSON-RPC 2.0 facade over one or more UseFramework instances."""

    def __init__(
        self,
        instances: Mapping[str, UseFramework] | None = None,
        layers: Iterable[str] = DEFAULT_LAYERS,
        title: str = "sincpro-rpc",
        version: str = "1.0.0",
    ):
        self._catalogs: dict[str, Catalog] = {}
        self._layers = tuple(layers)
        self._title = title
        self._version = version
        for alias, framework_instance in (instances or {}).items():
            self.add(alias, framework_instance)

    def add(
        self,
        alias: str,
        framework_instance: UseFramework,
        include: Iterable[type | str] | None = None,
        exclude: Iterable[type | str] | None = None,
        wrap: Mapping[type | str, Wrapper] | None = None,
    ) -> Self:
        catalog = Catalog(framework_instance)
        if include is not None:
            catalog.include(*include)
        if exclude is not None:
            catalog.exclude(*exclude)
        for dto, wrapper in (wrap or {}).items():
            catalog.wrap(dto, wrapper)
        self._catalogs[validate_alias(alias)] = catalog
        return self

    def methods(self) -> MethodIndex:
        return index_methods(self._catalogs, self._layers)

    def discover(self) -> dict[str, Any]:
        indexed = self.methods()
        published = [
            method_object(name, operation, alias)
            for name, (alias, _framework, operation) in indexed.items()
        ]
        return openrpc_document(self._title, published, version=self._version)

    def handle(
        self, payload: Any, context: Mapping[str, Any] | None = None
    ) -> dict[str, Any] | list[Any] | None:
        indexed = self.methods()
        return handle_payload(indexed, self.discover, payload, context)

    def is_healthy(self) -> bool:
        """Every registered `UseFramework` is still built.

        The same question `entrypoint_grpc`'s default `health_check` asks, so a
        process running both wires reports the same thing on either one. A gateway
        that failed to build never reaches `.routes()`/`.handle()` in the first
        place (`Catalog` already forces `build_root_bus()`), so this is a live
        re-check rather than a promise made once at construction time.
        """
        return all(
            catalog.framework_instance.was_initialized
            and catalog.framework_instance.bus is not None
            for catalog in self._catalogs.values()
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

        gateway = self

        async def rpc_endpoint(request: Request) -> Response:
            raw = await request.body()
            try:
                payload = json.loads(raw.decode("utf-8") or "null")
            except (UnicodeDecodeError, json.JSONDecodeError):
                return JSONResponse(
                    jsonrpc_error(PARSE_ERROR, "Parse error"), status_code=200
                )
            header_context = merge_http_context(request.headers)
            reply = gateway.handle(payload, context=header_context or None)
            if reply is None:
                return Response(status_code=204)
            return JSONResponse(reply)

        async def openrpc_endpoint(_request: Request) -> JSONResponse:
            return JSONResponse(gateway.discover())

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
    title: str = "sincpro-rpc",
    version: str = "1.0.0",
) -> Any:
    return RpcGateway(instances, layers=layers, title=title, version=version).app()
