"""`FastApiGateway`: the REST wire on FastAPI (PRD_15 §2) — one `APIRouter` per bounded context,
one OpenAPI document for generated and hand-written routes together. The `[fastapi]` extra.

    api = FastApiGateway([billing, sales])                   # resource: only what is declared
    api = FastApiGateway([billing], profile=Profile.RPC)           # every use case, RPC over HTTP
    api.group(billing, version="v1")                         # /v1/billing/...

    app = api.app()                                          # or, full control:
    app = FastAPI(separate_input_output_schemas=False)
    install_problem_handlers(app)
    app.include_router(api.router(billing, exclude=[CommandIssueInvoice]))
    assert api.verify(app) == []

Context: the routes, the app and what they break together are the wire's
(`adapters.fastapi.wire`); `Catalog.wrap` is refused: it decorates the dictionary-in call this
wire never makes.
"""

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

from anyio import CapacityLimiter

from sincpro_framework.entrypoints.adapters.fastapi import exceptions
from sincpro_framework.entrypoints.adapters.fastapi.wire import (
    FastApiWire,
    app_problems,
    fastapi_app,
    joined_router,
    operation_extra,
    problem_responses,
)
from sincpro_framework.entrypoints.domain.bindings import RestBinding
from sincpro_framework.entrypoints.domain.layers import Wrapper
from sincpro_framework.entrypoints.domain.surface import Exposure, Profile, Resolved
from sincpro_framework.entrypoints.services.gateway import DEFAULT_LAYERS, Buses, Gateway
from sincpro_framework.use_bus import UseFramework

if TYPE_CHECKING:
    from fastapi import APIRouter, FastAPI


class FastApiGateway(Gateway):
    """The REST wire on FastAPI — the buses' surface as `APIRouter`s, or as a whole app.

    Context: a project that serves HTTP with FastAPI publishes its use cases through this,
    and adds routes of its own beside them through `bus_call`; `verify(app)` checks the two
    together. What it builds is returned, never started.

        api = FastApiGateway([billing]).group(billing, version="v1")
        app = api.app()                 # /v1/billing/..., /healthz, problems installed
        uvicorn.run(app)
    """

    wire = "rest"

    def __init__(
        self,
        instances: Buses | None = None,
        layers: Iterable[str] = DEFAULT_LAYERS,
        title: str = "sincpro",
        version: str = "1.0.0",
        profile: Profile = Profile.RESOURCE,
        exposure: Exposure | None = None,
        unguarded: bool = False,
        worker_threads: int | None = None,
    ) -> None:
        """`Profile.RPC` publishes the catalog unless `exposure` says otherwise;
        `worker_threads` bounds the threads the bus runs on (anyio's default, 40)."""
        self._wire = FastApiWire(
            profile, CapacityLimiter(worker_threads) if worker_threads else None
        )
        mode = exposure or (Exposure.CATALOG if profile == Profile.RPC else Exposure.DECLARED)
        super().__init__(
            instances,
            layers,
            title,
            version,
            exposure=mode,
            unguarded=unguarded,
            port=self._wire,
        )

    @property
    def profile(self) -> Profile:
        """`Profile.RESOURCE` — only what is declared — or `Profile.RPC` — every use case,
        RPC over HTTP."""
        return self._wire.profile

    def add(
        self,
        alias: str | UseFramework,
        framework_instance: UseFramework | None = None,
        include: Iterable[type | str] | None = None,
        exclude: Iterable[type | str] | None = None,
        wrap: Mapping[type | str, Wrapper] | None = None,
    ) -> "FastApiGateway":
        """`Gateway.add`, with `wrap` refused: a route validates the DTO once and calls the
        bus, never the dictionary-in run a wrapper decorates — an interceptor does that job.
        """
        if wrap:
            raise exceptions.ProgrammingError(
                "FastApiGateway: wrap decorates the dictionary-in call this wire never makes — "
                "the route validates the DTO once and calls the bus; use an interceptor"
            )
        super().add(alias, framework_instance, include, exclude)
        return self

    def _surface_of(
        self, bus: UseFramework | str | None, exclude: Iterable[type]
    ) -> list[Resolved[RestBinding]]:
        """The validated surface narrowed to one context — the bus or its alias, every one
        when `None` — without the Commands in `exclude`."""
        left_out = set(exclude)
        return [
            one
            for one in self.surface()
            if one.operation.command not in left_out
            and (bus is None or one.operation.alias == bus or one.operation.bus is bus)
        ]

    def routers(self) -> "dict[str, APIRouter]":
        """One `APIRouter` per bounded context, by alias — each with its group's prefix."""
        return self.build()

    def router(
        self, bus: UseFramework | str | None = None, exclude: Iterable[type] = ()
    ) -> "APIRouter":
        """The routes of one context — the bus or its alias — or of every one, with their
        prefixes; `exclude` leaves out the Commands a project answers with routes of its own.

            gateway.router("billing")         ->  APIRouter(prefix="/v1/billing")
            gateway.router()                  ->  one APIRouter including every context's
            gateway.router(billing, exclude=[CommandIssueInvoice])
            ->  billing's routes but POST /invoices, answered by a route of the project's
            gateway.router("shipping")        ->  ProgrammingError: no bus of this gateway

        A bus that has nothing exposed answers an empty router, not an error.
        """
        surface = self._surface_of(bus, exclude)
        if (
            bus is not None
            and not surface
            and not any(
                alias == bus or catalog.framework_instance is bus
                for alias, catalog in self.catalogs.items()
            )
        ):
            raise exceptions.ProgrammingError(
                f"router({bus!r}): no bus of this gateway is {bus!r}"
            )
        built = self._wire.build(surface)
        if bus is not None and len(built) == 1:
            return next(iter(built.values()))
        return joined_router(built.values())

    def operation_extra(self, bus: UseFramework, command: type) -> dict[str, Any]:
        """The module's `operation_extra`, reachable from the gateway a project holds."""
        return operation_extra(bus, command)

    def problem_responses(
        self, bus: UseFramework, command: type
    ) -> dict[int | str, dict[str, Any]]:
        """The module's `problem_responses`, with no declared errors."""
        return problem_responses(bus, command)

    def app(
        self,
        health_path: str | None = "/healthz",
        metrics_path: str | None = "/metrics",
        **fastapi_kwargs: Any,
    ) -> "FastAPI":
        """Every context's router in one FastAPI app, its problem handlers installed, a health
        check, and — when the process's metrics are scraped (`PrometheusRecorder`) — the scrape
        at `metrics_path` (`wire.fastapi_app`). `fastapi_kwargs` go to `FastAPI(...)`.

            gateway.app()                     ->  FastAPI with /healthz and every route
            gateway.app(health_path=None, docs_url=None)
            ->  no health check, no Swagger UI
        """
        return fastapi_app(
            self._title,
            self._version,
            self.router(),
            self.is_healthy,
            health_path,
            metrics_path,
            **fastapi_kwargs,
        )

    def verify(self, app: "FastAPI | None" = None) -> list[str]:
        """Every reason the surface would be refused — and, given `app`, what its routes break
        together, generated and hand-written (`wire.app_problems`).

            gateway.verify()                  ->  []  (the surface alone)
            gateway.verify(gateway.app())     ->  []
            gateway.verify(<app with a route of its own on POST /v1/billing/invoices>)
            ->  ["rest: POST /v1/billing/invoices answered twice — ... and issue_by_hand"]

        1. What `Gateway.verify` finds in the surface; no `app`, that is all.
        Final: with what the app breaks after it, each problem once, in the order found.
        """
        problems = super().verify()
        if app is None:
            return problems
        return list(dict.fromkeys([*problems, *app_problems(app)]))
