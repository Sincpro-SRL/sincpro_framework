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

from sincpro_framework.auth.domain.identity import Credentials
from sincpro_framework.auth.entrypoint.transports import challenges_of, credentials_from_asgi
from sincpro_framework.context.domain.level import EntrypointKind
from sincpro_framework.entrypoints.adapters.rest import exceptions
from sincpro_framework.entrypoints.adapters.rest.openapi import openapi_document
from sincpro_framework.entrypoints.adapters.rest.routing import (
    RestRoute,
    build_routes,
    failure_answer,
    payload_from_query,
)
from sincpro_framework.entrypoints.entrypoint.gateway import DEFAULT_LAYERS, Buses, Gateway
from sincpro_framework.entrypoints.infrastructure.http import merge_http_context
from sincpro_framework.entrypoints.services.scalar_executor import execute
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
    """The REST wire on Starlette — every published use case a route, the whole described by
    one OpenAPI 3.1 document.

    Context: the catalog wire of PRD_12 — it names no `wire`, so it publishes every use case
    of its layers with no declaration; `FastApiGateway` is the declared one. Its route table
    and its document are built without a web library; Starlette is only what serves them.

        RestGateway([billing], prefix="/api/v1").rest_routes()
        ->  [RestRoute(path="/api/v1/billing/command-issue-invoice", methods=("POST",), ...),
             RestRoute(path="/api/v1/billing/query-invoices", methods=("GET", "POST"), ...)]
    """

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
        """An alias is a path segment: letters, digits, `_` and `-`, not starting with a
        symbol — refused with `ValueError` otherwise."""
        if not ALIAS_PATTERN.match(alias):
            raise ValueError(f"REST alias [{alias}] must match {ALIAS_PATTERN.pattern}")
        return alias

    def alias_for(self, framework_instance: UseFramework) -> str:
        """The bus's name with anything a path segment refuses turned into `-`.

            alias_for(<bus "billing.v2">)  ->  "billing-v2"

        What a bus added without an alias is published under.
        """
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
        """The OpenAPI 3.1 document of `rest_routes()` — what `/openapi.json` serves;
        `servers` are the base URLs a generated client may call."""
        return openapi_document(self._title, self._version, self.rest_routes(), servers)

    def challenges(self) -> list[str]:
        """The `WWW-Authenticate` challenges a 401 carries — one per provider of the guarded
        buses."""
        return challenges_of(self.buses())

    def call(
        self,
        route: RestRoute,
        payload: Mapping[str, Any],
        context: Mapping[str, Any] | None = None,
        credentials: Credentials | None = None,
    ) -> tuple[int, Any]:
        """One call of `route`, answered as `(status, body)` — what a web library turns into a
        response; the transport-free path a test or another host takes.

            gateway.call(<POST /billing/command-issue-invoice>, {"total": 10})
            ->  (200, {"invoice_id": "F-1", "total": 10})
            gateway.call(<same route>, {"total": "ten"})
            ->  (422, {"error": {"kind": "invalid", "detail": [...]}})

        A 500 is logged here unless a bus already reported it; the body never says why.
        """
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
        """The payload a request carries for `route`, before any validation — the DTO's.

            GET  /billing/invoices/F-9?status=open
            ->  {"status": "open", "invoice_id": "F-9"}
            POST /billing/command-issue-invoice  {"total": 10}  ->  {"total": 10}

        1. `GET`: the query string, each non-plain field decoded from JSON.
        2. Any other method: the body, a JSON object — or `InvalidRequest` (400).
        Final: the path's fields over either.
        """
        if request.method == "GET":
            payload = payload_from_query(
                dict(request.query_params), route.operation.json_schema
            )
        else:
            raw = await request.body()
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else {}
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise exceptions.InvalidRequest(f"the body is not JSON: {error}") from error
            if not isinstance(payload, dict):
                raise exceptions.InvalidRequest(
                    "the body is a JSON object of the DTO's fields"
                )
        return {**payload, **request.path_params}

    def routes(
        self,
        openapi_path: str | None = "/openapi.json",
        docs_path: str | None = "/docs",
        health_path: str | None = "/healthz",
    ) -> list[Any]:
        """This gateway's Starlette routes, mountable into any app — every use case, and, unless
        `None`, the OpenAPI document, its docs page and a health check.

            gateway.routes()
            ->  [Route("/billing/command-issue-invoice", methods=["POST"]), ...,
                 Route("/openapi.json"), Route("/docs"), Route("/healthz")]
            gateway.routes(openapi_path=None, health_path=None)  ->  the use cases only

        1. Starlette imported — refused with the extra-install hint without it.
        2. One `Route` per REST route, named by its `operationId`, whose endpoint:
           2.1 reads the payload (`_payload`) — an unreadable one answered 400;
           2.2 merges the context the caller carried (`merge_http_context`);
           2.3 runs `call` in a worker thread, with the request's credentials;
           2.4 answers its `(status, body)` as JSON — a 401 with the challenges.
        3. The OpenAPI document at `openapi_path`, built per request.
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
        gateway = self

        def endpoint(route: RestRoute) -> Any:
            """The Starlette endpoint of one route."""

            async def answer(request: Request) -> JSONResponse:
                """Answer one request on this route.

                1. The payload out of the request — a 400 when it cannot be read.
                2. The context the caller carried (`merge_http_context`).
                3. `gateway.call` in a worker thread, with the request's credentials.
                Final: its `(status, body)` as JSON — a 401 with the buses' challenges in
                `WWW-Authenticate`.
                """
                try:
                    payload = await gateway._payload(request, route)
                except exceptions.InvalidRequest as error:
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
                """The OpenAPI document, built per request — it follows the catalog."""
                return JSONResponse(gateway.openapi())

            served.append(Route(openapi_path, document, methods=["GET"]))
        if docs_path is not None and openapi_path is not None:
            page = DOCS_PAGE.format(title=self._title, openapi=openapi_path)

            async def docs(_request: Request) -> HTMLResponse:
                """A Swagger UI page reading `openapi_path`."""
                return HTMLResponse(page)

            served.append(Route(docs_path, docs, methods=["GET"]))
        if health_path is not None:

            async def health(_request: Request) -> JSONResponse:
                """200 `{"status": "ok"}` while every bus is built, else 503 `unhealthy`."""
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
        """Serve `app()` with uvicorn until stopped — a quick start; a deployment serves
        `app()` with its own server and settings. `kwargs` go to `uvicorn.run`."""
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
    """`RestGateway(...).app()` in one call — the buses served alone, nothing to compose.

    uvicorn.run(build_rest_app([billing], prefix="/api/v1"))
    """
    return RestGateway(instances, layers, title, version, prefix).app()
