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
with `/openapi.json`, a `/docs` page and a health check. The route table and the document are the
wire's (`adapters.rest`); Starlette is imported only when routes are asked for (the `[rest]`
extra).
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from sincpro_framework.auth import Credentials, security
from sincpro_framework.entrypoints.adapters.rest import routing, serving
from sincpro_framework.entrypoints.adapters.rest.openapi import openapi_document
from sincpro_framework.entrypoints.adapters.rest.routing import RestRoute, build_routes
from sincpro_framework.entrypoints.services.gateway import DEFAULT_LAYERS, Buses, Gateway
from sincpro_framework.use_bus import UseFramework

ALIAS_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


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
        return security.challenges(self.buses())

    def call(
        self,
        route: RestRoute,
        payload: Mapping[str, Any],
        context: Mapping[str, Any] | None = None,
        credentials: Credentials | None = None,
    ) -> tuple[int, Any]:
        """One call of `route`, answered as `(status, body)` (`routing.call_route`) — what a web
        library turns into a response; the transport-free path a test or another host takes.

            gateway.call(<POST /billing/command-issue-invoice>, {"total": 10})
            ->  (200, {"invoice_id": "F-1", "total": 10})
            gateway.call(<same route>, {"total": "ten"})
            ->  (422, {"error": {"kind": "invalid", "detail": [...]}})
        """
        return routing.call_route(route, payload, context, credentials)

    def routes(
        self,
        openapi_path: str | None = "/openapi.json",
        docs_path: str | None = "/docs",
        health_path: str | None = "/healthz",
    ) -> list[Any]:
        """This gateway's Starlette routes, mountable into any app — every use case, and, unless
        `None`, the OpenAPI document, its docs page and a health check (`serving.routes`).

            gateway.routes()
            ->  [Route("/billing/command-issue-invoice", methods=["POST"]), ...,
                 Route("/openapi.json"), Route("/docs"), Route("/healthz")]
            gateway.routes(openapi_path=None, health_path=None)  ->  the use cases only
        """
        return serving.routes(
            self.rest_routes(),
            self.challenges,
            self.openapi,
            self.is_healthy,
            self._title,
            openapi_path,
            docs_path,
            health_path,
        )

    def app(
        self,
        middleware: Sequence[Any] | None = None,
        routes: Sequence[Any] | None = None,
        lifespan: Any | None = None,
        **starlette_kwargs: Any,
    ) -> Any:
        """A standalone ASGI app of `routes()` — `middleware` (CORS, `IdentityMiddleware`, a
        rate limit), extra `routes`, a `lifespan`, and anything else Starlette takes."""
        return serving.app(
            [*self.routes(), *(routes or ())], middleware, lifespan, **starlette_kwargs
        )

    def run(self, host: str = "127.0.0.1", port: int = 8080, **kwargs: Any) -> None:
        """Serve `app()` with uvicorn until stopped — a quick start; a deployment serves
        `app()` with its own server and settings. `kwargs` go to `uvicorn.run`."""
        serving.run(self.app(), host, port, **kwargs)
