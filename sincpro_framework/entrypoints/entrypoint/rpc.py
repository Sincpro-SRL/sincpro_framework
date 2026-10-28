"""`RpcGateway`: the declared surface of one or more buses as JSON-RPC 2.0 methods named
`{namespace}.{operation}` (PRD_15 §4) — the naming and `dispatch` are the wire's (`adapters.rpc`),
the Starlette routes, app and uvicorn its serving.

    rpc = RpcGateway([billing]).group(billing, version="v2")    # billing.v2.issue_invoice
    uvicorn.run(rpc.app())                                      # POST /rpc, GET /openrpc.json
    app.router.routes += rpc.routes()                            # inside an app of the project's
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from sincpro_framework.auth import Credentials, security
from sincpro_framework.entrypoints.adapters.rpc import serving
from sincpro_framework.entrypoints.adapters.rpc.jrpc import (
    DEFAULT_MAX_BATCH_SIZE,
    DEFAULT_MAX_BODY_BYTES,
    RESERVED_PREFIX,
)
from sincpro_framework.entrypoints.adapters.rpc.wire import (
    JsonRpcWire,
    Reply,
    RpcSurface,
    dispatch,
)
from sincpro_framework.entrypoints.domain.bindings import RpcBinding
from sincpro_framework.entrypoints.domain.surface import Exposure, Resolved, Wire
from sincpro_framework.entrypoints.services.gateway import DEFAULT_LAYERS, Buses, Gateway
from sincpro_framework.use_bus import UseFramework

ALIAS_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")


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
        exposure: Exposure = Exposure.DECLARED,
        unguarded: bool = False,
        port: Wire[Any] | None = None,
    ):
        """Without a `port`, a `JsonRpcWire` of `title`, `version` and the limits (each its
        default when `None`); with one, it must be a `JsonRpcWire` and those four are
        refused — they are the port's.

            RpcGateway([billing], max_batch_size=10)
            RpcGateway([billing], port=MyNaming(title="billing-api"))
        """
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
        """How many calls one batch may run — the wire's."""
        return self._wire.max_batch_size

    @property
    def max_body_bytes(self) -> int:
        """How many bytes of a body are read before answering 413 — the wire's."""
        return self._wire.max_body_bytes

    def validate_alias(self, alias: str) -> str:
        """An alias the JSON-RPC wire accepts — a letter first, then letters, digits, `_`, `-`
        — refused with `ValueError` otherwise, and when it is `rpc`.

        Context: an alias is the namespace of its methods by default, so `rpc` would publish
        `rpc.issue_invoice` under the prefix JSON-RPC 2.0 reserves for the protocol itself.

            validate_alias("billing")  ->  "billing"
            validate_alias("RPC")      ->  ValueError: ... is reserved
        """
        if not ALIAS_PATTERN.match(alias):
            raise ValueError(
                f"RPC instance alias [{alias}] must match {ALIAS_PATTERN.pattern}"
            )
        if f"{alias}.".lower() == RESERVED_PREFIX:
            raise ValueError(
                f"RPC instance alias [{alias}] is reserved: JSON-RPC 2.0 reserves method names "
                f"beginning [{RESERVED_PREFIX}] for the protocol (only rpc.discover here)"
            )
        return alias

    def alias_for(self, framework_instance: UseFramework) -> str:
        """The bus's name with anything an alias refuses turned into `-` — the namespace then
        makes it snake_case.

            alias_for(<bus "billing.v2">)  ->  "billing-v2"   # methods: billing_v2.<op>
        """
        return re.sub(r"[^A-Za-z0-9_-]", "-", framework_instance.name)

    def build(self) -> RpcSurface:
        """The validated surface as the wire serves it — built once per resolution, refused
        with `ProgrammingError` (every reason listed) when it does not hold.

            gateway.build().methods      ->  {"billing.issue_invoice": <resolved>, ...}
            gateway.build() is gateway.build()      ->  True, until resolved again

        A new resolution — a bus added, a binding changed — builds it again.
        """
        surface: Sequence[Resolved[RpcBinding]] = self.surface()
        resolved = self._resolved
        if self._built is None or self._built[0] is not resolved:
            self._built = (resolved, self._wire.build(surface))
        return self._built[1]

    def methods(self) -> Mapping[str, Resolved[RpcBinding]]:
        """Each published method by its name."""
        return self.build().methods

    def discover(self) -> dict[str, Any]:
        """The OpenRPC document — what `rpc.discover` and `GET /openrpc.json` answer."""
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
        return security.challenges(
            catalog.framework_instance for catalog in self._catalogs.values()
        )

    def health_route(self, path: str = "/healthz") -> Any:
        """A `GET` route answering `200 {"status": "ok"}` / `503 {"status": "unhealthy"}`
        from `is_healthy()` (`serving.health_route`). Included in `.routes()`/`.app()` by
        default; `health_path=None` on either drops it for a host that wires its own."""
        return serving.health_route(self.is_healthy, path)

    def ready_route(self, path: str = "/readyz") -> Any:
        """A `GET` route answering 200 and every check of `readiness` while each passes, 503 and
        the problem naming those that failed (`serving.ready_route`). Included in
        `.routes()`/`.app()` by default; `ready_path=None` on either drops it."""
        return serving.ready_route(self.readiness.answer, path)

    def routes(
        self,
        rpc_path: str = "/rpc",
        discover_path: str = "/openrpc.json",
        health_path: str | None = "/healthz",
        ready_path: str | None = "/readyz",
    ) -> list[Any]:
        """This gateway's Starlette `Route`s, mountable into any app: JSON-RPC, OpenRPC
        discovery and — by default — the liveness and readiness checks (`serving.routes`).

        The composable primitive: `Starlette(routes=[*gateway.routes(), *my_own_routes])`,
        or `Mount("/payments", routes=gateway.routes())` to namespace one gateway among
        several. `.app()` is this same list with no host around it supplied.

            gateway.routes()
            ->  [Route("/rpc", POST), Route("/openrpc.json", GET), Route("/healthz", GET),
                 Route("/readyz", GET)]

        1. The surface built once (`build`) — the routes serve that one.
        Final: its routes, with the liveness and readiness routes unless their path is `None`.
        """
        probes = [
            *([self.health_route(health_path)] if health_path is not None else []),
            *([self.ready_route(ready_path)] if ready_path is not None else []),
        ]
        return serving.routes(self.build(), rpc_path, discover_path, probes)

    def app(
        self,
        middleware: Sequence[Any] | None = None,
        routes: Sequence[Any] | None = None,
        lifespan: Any | None = None,
        health_path: str | None = "/healthz",
        ready_path: str | None = "/readyz",
        **starlette_kwargs: Any,
    ) -> Any:
        """A standalone ASGI app: `POST /rpc` (JSON-RPC 2.0), `GET /openrpc.json`,
        and — by default — `GET /healthz` and `GET /readyz`.

        The convenience path, for a process that serves only this gateway. `middleware` is a
        list of Starlette `Middleware(...)` instances (CORS, an auth check, request logging);
        `routes` are extra routes served alongside this gateway's own (another gateway's
        `.routes()`, a metrics endpoint); `lifespan` is a Starlette lifespan context manager
        for startup/shutdown (opening a connection pool, warming a cache); `health_path=None` and
        `ready_path=None` drop the liveness and readiness routes. Anything else `starlette.applications.Starlette` accepts
        passes through in `starlette_kwargs`.

        A process that wants a different CORS policy per set of buses composes one `Starlette`
        of its own instead: one `RpcGateway` (with its own `middleware=`) per trust domain,
        `Mount`ed at a different path or served on a different port — CORS is a property of
        the origin/port a browser talks to, not of which bus a JSON-RPC `method` happens to
        name inside the body.
        """
        return serving.app(
            [*self.routes(health_path=health_path, ready_path=ready_path), *(routes or ())],
            middleware,
            lifespan,
            **starlette_kwargs,
        )

    def run(self, host: str = "127.0.0.1", port: int = 8080, **kwargs: Any) -> None:
        """Start uvicorn on the JSON-RPC ASGI app. Extra kwargs go to uvicorn.run.

        For CORS, extra routes, a lifespan, or mounting several gateways in one process, build
        the app with `.app(middleware=..., routes=..., lifespan=...)` (or `.routes()` into a
        Starlette/app of your own) and pass that to `uvicorn.run(...)` directly instead of
        calling `.run()`.
        """
        serving.run(self.app(), host, port, **kwargs)
