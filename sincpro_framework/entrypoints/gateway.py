"""`Gateway`: what every entrypoint shares — the buses it serves, each as a filtered catalog, and
the one way to say which use cases a wire publishes.

    RestGateway([billing, sales])                          # automatic: every bus, every use case
    RestGateway([billing, sales], layers=("app_services",))   # one layer only
    RestGateway().add(billing, exclude=[CommandReconcile]).add("ventas", sales, include=[...])

Context: the same on every wire — JSON-RPC, gRPC, MCP, REST — so exposing a bus is learned once.
A list of buses is the automatic mode: each is published under its own name, made fit for the
wire (`sincpro-billing` is `sincpro_billing` for a gRPC package). `add` is the fine-grained one:
an alias of the caller's, `include` / `exclude` by Command or name, `wrap` to decorate one use
case's run. A use case marked `entrypoints.internal` is on no wire, whatever is included. What a
gateway builds — an ASGI app, its routes, a gRPC server, a FastMCP server — is returned, never
started, so a project adds its middleware, interceptors and plugins to it.
"""

from collections.abc import Iterable, Mapping
from typing import Self

from sincpro_framework.entrypoints.catalog import Catalog, PackedFeatureOrAppService
from sincpro_framework.entrypoints.const import Layer, Wrapper
from sincpro_framework.use_bus import UseFramework

DEFAULT_LAYERS = (Layer.APP_SERVICES, Layer.FEATURES)

type Buses = Mapping[str, UseFramework] | Iterable[UseFramework]
"""`{"billing": billing}` names each bus; `[billing, sales]` publishes each under its own name."""


class Gateway:
    def __init__(
        self,
        instances: Buses | None = None,
        layers: Iterable[str] = DEFAULT_LAYERS,
        title: str = "sincpro",
        version: str = "1.0.0",
    ) -> None:
        self._catalogs: dict[str, Catalog] = {}
        self._layers = tuple(layers)
        self._title = title
        self._version = version
        named = (
            instances.items()
            if isinstance(instances, Mapping)
            else ((None, one) for one in instances or ())
        )
        for alias, framework_instance in named:
            self.add(alias or framework_instance, framework_instance)

    def validate_alias(self, alias: str) -> str:
        """What this wire accepts as a bus's alias — refused with the rule it breaks."""
        return alias

    def alias_for(self, framework_instance: UseFramework) -> str:
        """The alias a bus added by itself is published under — its name, fit for the wire."""
        return framework_instance.name

    def added(self, alias: str, framework_instance: UseFramework) -> None:
        """Called once a bus is added — what a wire checks or warns about one."""

    def add(
        self,
        alias: str | UseFramework,
        framework_instance: UseFramework | None = None,
        include: Iterable[type | str] | None = None,
        exclude: Iterable[type | str] | None = None,
        wrap: Mapping[type | str, Wrapper] | None = None,
    ) -> Self:
        """Publish a bus — `add(billing)` under its own name, `add("ventas", sales)` under one of
        the caller's — narrowed by `include` / `exclude` and with `wrap`ped runs."""
        if isinstance(alias, UseFramework):
            framework_instance, alias = alias, self.alias_for(alias)
        if framework_instance is None:
            raise TypeError(f"add({alias!r}) names no bus: add({alias!r}, bus) or add(bus)")
        catalog = Catalog(framework_instance)
        if include is not None:
            catalog.include(*include)
        if exclude is not None:
            catalog.exclude(*exclude)
        for dto, wrapper in (wrap or {}).items():
            catalog.wrap(dto, wrapper)
        validated = self.validate_alias(alias)
        self._catalogs[validated] = catalog
        self.added(validated, framework_instance)
        return self

    @property
    def catalogs(self) -> Mapping[str, Catalog]:
        return dict(self._catalogs)

    def buses(self) -> list[UseFramework]:
        return [catalog.framework_instance for catalog in self._catalogs.values()]

    def operations(self) -> list[tuple[str, UseFramework, PackedFeatureOrAppService]]:
        """Every published use case, with the alias and bus it belongs to, in the layers asked."""
        return [
            (alias, catalog.framework_instance, operation)
            for alias, catalog in self._catalogs.items()
            for operation in catalog.get_scalar_use_cases(filter_binaries_schema=True)
            if operation.layer in self._layers
        ]

    def is_healthy(self) -> bool:
        """Every bus is still built — what every wire's health check answers."""
        return all(one.was_initialized and one.bus is not None for one in self.buses())
