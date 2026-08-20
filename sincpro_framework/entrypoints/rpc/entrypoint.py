"""JSON-RPC gateway: wrap one or more UseFramework instances as JSON-RPC 2.0 methods."""

import json
import re
from collections.abc import Iterable, Mapping
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

    def app(self) -> Any:
        """ASGI app: POST /rpc (JSON-RPC 2.0) and GET /openrpc.json (OpenRPC 1.4)."""
        try:
            from starlette.applications import Starlette  # pyright: ignore
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

        return Starlette(
            routes=[
                Route("/rpc", rpc_endpoint, methods=["POST"]),
                Route("/openrpc.json", openrpc_endpoint, methods=["GET"]),
            ]
        )

    def run(self, host: str = "127.0.0.1", port: int = 8080, **kwargs: Any) -> None:
        """Start uvicorn on the JSON-RPC ASGI app. Extra kwargs go to uvicorn.run."""
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
