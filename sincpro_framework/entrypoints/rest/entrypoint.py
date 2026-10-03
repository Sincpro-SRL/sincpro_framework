"""`RestGateway`: the buses' use cases as HTTP resources, described by OpenAPI 3.1.

    rest = RestGateway([billing, sales], prefix="/api/v1")          # automatic: every use case
    rest.add("ventas", sales, exclude=[CommandReconcile])             # or one bus, narrowed
    rest.route(QueryInvoice, "GET /billing/invoices/{invoice_id}")    # a path of the project's

    uvicorn.run(rest.app(middleware=[Middleware(CORSMiddleware, ...)]))
    app.router.routes += rest.routes()                                 # inside a FastAPI / Starlette app

Context: a Command is `POST`, a `Query` is `GET` — cacheable, its fields in the query string —
and `POST` to the same path for a criteria too long for a URL. Every call is authenticated by the
bus's `AccessControl`, as on every wire. `routes()` is the composable primitive — mount it,
namespace it, wrap it with middleware of the project's; `app()` is those routes served alone,
with `/openapi.json`, a `/docs` page and a health check. Starlette is imported only when routes
are asked for (the `[rest]` extra).
"""

import asyncio
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from sincpro_framework.auth.domain import Credentials
from sincpro_framework.auth.transports import challenges_of, credentials_from_asgi
from sincpro_framework.context.domain.level import EntrypointKind
from sincpro_framework.entrypoints.gateway import DEFAULT_LAYERS, Buses, Gateway
from sincpro_framework.entrypoints.rest.openapi import openapi_document
from sincpro_framework.entrypoints.rest.routing import (
    InvalidRequest,
    RestRoute,
    build_routes,
    failure_answer,
    payload_from_query,
)
from sincpro_framework.entrypoints.rpc.entrypoint import merge_http_context
from sincpro_framework.entrypoints.scalar_executor import execute
from sincpro_framework.observability import process
from sincpro_framework.sincpro_logger import logger
from sincpro_framework.use_bus import UseFramework

REST_MISSING = (
    "Starlette/uvicorn is not installed. Install with: pip install sincpro-framework[rest]"
)
ALIAS_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
DOCS_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>{title}</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css"></head>
<body><div id="ui"></div>
<script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
<script>SwaggerUIBundle({{url: "{openapi}", dom_id: "#ui"}});</script></body></html>"""


class RestGateway(Gateway):
    def __init__(
        self,
        instances: Buses | None = None,
        layers: Iterable[str] = DEFAULT_LAYERS,
        title: str = "sincpro-rest",
        version: str = "1.0.0",
        prefix: str = "",
    ):
        """`prefix` goes before every path — `/api/v1` — so a version is in the URL from day
        one."""
        self._prefix = prefix.rstrip("/")
        self._overrides: dict[str, str] = {}
        super().__init__(instances, layers, title, version)

    def validate_alias(self, alias: str) -> str:
        if not ALIAS_PATTERN.match(alias):
            raise ValueError(f"REST alias [{alias}] must match {ALIAS_PATTERN.pattern}")
        return alias

    def alias_for(self, framework_instance: UseFramework) -> str:
        return re.sub(r"[^A-Za-z0-9_-]", "-", framework_instance.name)

    def route(self, dto: type | str, spec: str) -> "RestGateway":
        """Answer `dto` on a route of the project's — `"GET /billing/invoices/{invoice_id}"`,
        under the prefix; the path's names are the DTO's fields."""
        self._overrides[dto if isinstance(dto, str) else dto.__name__] = spec
        return self

    def rest_routes(self) -> list[RestRoute]:
        """Every published use case's route — the table `routes()` serves and `openapi()`
        describes."""
        return build_routes(self.operations(), self._prefix, self._overrides)

    def openapi(self, servers: Iterable[str] = ()) -> dict[str, Any]:
        return openapi_document(self._title, self._version, self.rest_routes(), servers)

    def challenges(self) -> list[str]:
        return challenges_of(self.buses())

    def call(
        self,
        route: RestRoute,
        payload: Mapping[str, Any],
        context: Mapping[str, Any] | None = None,
        credentials: Credentials | None = None,
    ) -> tuple[int, Any]:
        """One call of `route`, answered as `(status, body)` — what a web library turns into a
        response; the transport-free path a test or another host takes."""
        try:
            return 200, execute(
                route.bus,
                route.operation.run,
                dict(payload),
                context,
                credentials,
                EntrypointKind.REST,
            )
        except Exception as error:
            status, body = failure_answer(error)
            if status == 500 and not process.was_reported(error):
                logger.exception("REST %s failed", route.path)
            return status, {"error": body}

    async def _payload(self, request: Any, route: RestRoute) -> dict[str, Any]:
        if request.method == "GET":
            payload = payload_from_query(
                dict(request.query_params), route.operation.json_schema
            )
        else:
            raw = await request.body()
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else {}
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise InvalidRequest(f"the body is not JSON: {error}") from error
            if not isinstance(payload, dict):
                raise InvalidRequest("the body is a JSON object of the DTO's fields")
        return {**payload, **request.path_params}

    def routes(
        self,
        openapi_path: str | None = "/openapi.json",
        docs_path: str | None = "/docs",
        health_path: str | None = "/healthz",
    ) -> list[Any]:
        """This gateway's Starlette routes, mountable into any app — every use case, and, unless
        `None`, the OpenAPI document, its docs page and a health check."""
        try:
            from starlette.requests import Request  # pyright: ignore[reportMissingImports]
            from starlette.responses import (  # pyright: ignore[reportMissingImports]
                HTMLResponse,
                JSONResponse,
            )
            from starlette.routing import Route  # pyright: ignore[reportMissingImports]
        except ImportError as error:
            raise ImportError(REST_MISSING) from error
        gateway = self

        def endpoint(route: RestRoute) -> Any:
            async def answer(request: Request) -> JSONResponse:
                try:
                    payload = await gateway._payload(request, route)
                except InvalidRequest as error:
                    status, body = failure_answer(error)
                    return JSONResponse({"error": body}, status_code=status)
                context = merge_http_context(request.headers) or None
                status, body = await asyncio.to_thread(
                    gateway.call,
                    route,
                    payload,
                    context,
                    credentials_from_asgi(request.scope),
                )
                headers = None
                if status == 401 and (challenges := gateway.challenges()):
                    headers = {"www-authenticate": ", ".join(challenges)}
                return JSONResponse(body, status_code=status, headers=headers)

            return answer

        served = [
            Route(
                route.path,
                endpoint(route),
                methods=list(route.methods),
                name=route.operation_id,
            )
            for route in self.rest_routes()
        ]
        if openapi_path is not None:

            async def document(_request: Request) -> JSONResponse:
                return JSONResponse(gateway.openapi())

            served.append(Route(openapi_path, document, methods=["GET"]))
        if docs_path is not None and openapi_path is not None:
            page = DOCS_PAGE.format(title=self._title, openapi=openapi_path)

            async def docs(_request: Request) -> HTMLResponse:
                return HTMLResponse(page)

            served.append(Route(docs_path, docs, methods=["GET"]))
        if health_path is not None:

            async def health(_request: Request) -> JSONResponse:
                healthy = gateway.is_healthy()
                return JSONResponse(
                    {"status": "ok" if healthy else "unhealthy"},
                    status_code=200 if healthy else 503,
                )

            served.append(Route(health_path, health, methods=["GET"]))
        return served

    def app(
        self,
        middleware: Sequence[Any] | None = None,
        routes: Sequence[Any] | None = None,
        lifespan: Any | None = None,
        **starlette_kwargs: Any,
    ) -> Any:
        """A standalone ASGI app of `routes()` — `middleware` (CORS, `IdentityMiddleware`, a
        rate limit), extra `routes`, a `lifespan`, and anything else Starlette takes."""
        try:
            from starlette.applications import Starlette  # pyright: ignore
        except ImportError as error:
            raise ImportError(REST_MISSING) from error
        return Starlette(
            routes=[*self.routes(), *(routes or ())],
            middleware=list(middleware) if middleware else None,
            lifespan=lifespan,
            **starlette_kwargs,
        )

    def run(self, host: str = "127.0.0.1", port: int = 8080, **kwargs: Any) -> None:
        try:
            import uvicorn  # pyright: ignore[reportMissingImports]
        except ImportError as error:
            raise ImportError(REST_MISSING) from error
        uvicorn.run(self.app(), host=host, port=port, **kwargs)


def build_rest_app(
    instances: Buses,
    layers: Iterable[str] = DEFAULT_LAYERS,
    title: str = "sincpro-rest",
    version: str = "1.0.0",
    prefix: str = "",
) -> Any:
    return RestGateway(instances, layers, title, version, prefix).app()


__all__ = ["RestGateway", "build_rest_app"]
