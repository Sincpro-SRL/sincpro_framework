"""MCP entrypoint: the declared surface (`McpGateway`, PRD_14) and the catalog of one bus
(`Entrypoint`, PRD_12), both on FastMCP (mcp.py) — the tools' names and hints come from the wire
(wire.py), every call goes through the bus."""

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any, Self

from sincpro_framework.data_layer.caching import declares_once
from sincpro_framework.ddd.query import Query
from sincpro_framework.entrypoints.adapters.mcp.auth import token_verifier
from sincpro_framework.entrypoints.adapters.mcp.mcp import (
    FASTMCP_MISSING,
    fastmcp_callable,
    tool_function,
)
from sincpro_framework.entrypoints.adapters.mcp.wire import McpTool, McpWire, hints_of
from sincpro_framework.entrypoints.domain.bindings import McpBinding
from sincpro_framework.entrypoints.domain.surface import Exposure
from sincpro_framework.entrypoints.entrypoint.catalog import (
    Catalog,
    PackedFeatureOrAppService,
)
from sincpro_framework.entrypoints.entrypoint.gateway import DEFAULT_LAYERS, Buses, Gateway
from sincpro_framework.entrypoints.infrastructure.registry import registry
from sincpro_framework.use_bus import UseFramework

if TYPE_CHECKING:
    from sincpro_framework.auth.entrypoint.access_control import AccessControl


def _catalog_hints(operation: PackedFeatureOrAppService) -> dict[str, Any]:
    """The hints of a catalog entry: what its facts prove, and — when its handler declares
    `@mcp` — its declared title, `destructive` and `open_world`; never its name, which stays
    the DTO's in the catalog."""
    handler = operation.handler or operation.dto
    declared = registry.of(handler).get(McpBinding.wire)
    is_query = isinstance(operation.dto, type) and issubclass(operation.dto, Query)
    fields = (
        {}
        if declared is None
        else {name: getattr(declared, name) for name in declared.model_fields_set}
    )
    fields.pop("name", None)
    binding = McpBinding(**{"read_only": is_query, "destructive": not is_query, **fields})
    return hints_of(binding, declares_once(handler))


class Entrypoint:
    """MCP facade over one UseFramework instance — PRD_12's catalog: every JSON-safe use case a
    tool named by its DTO class, with no declaration needed. The declared surface, over one bus
    or several, is `McpGateway`."""

    def __init__(self, framework_instance: UseFramework):
        self.catalog = Catalog(framework_instance)

    @property
    def name(self) -> str:
        return self.catalog.framework_instance._logger_name

    def include(self, *dtos: type | str) -> Self:
        self.catalog.include(*dtos)
        return self

    def exclude(self, *dtos: type | str) -> Self:
        self.catalog.exclude(*dtos)
        return self

    def wrap(self, dto: type | str, wrapper: Callable) -> Self:
        self.catalog.wrap(dto, wrapper)
        return self

    def tools(self) -> list[PackedFeatureOrAppService]:
        return self.catalog.get_scalar_use_cases()

    def to_callables(self) -> dict[str, Callable[[dict[str, Any]], dict[str, Any]]]:
        return {
            operation.name: operation.run for operation in self.catalog.get_scalar_use_cases()
        }

    def server(
        self,
        name: str | None = None,
        auth: "AccessControl[Any] | None" = None,
        base_url: str | None = None,
    ) -> Any:
        """Publish JSON-safe Features/ApplicationServices on a FastMCP server.

        1. Import FastMCP or raise with the extra-install hint.
        2. Register catalog.get_scalar_use_cases(filter_binaries_schema=True)
           with mcp.tool(fn, name=..., description=..., tags=layer, annotations=...) — the
           hints derived as `McpGateway` derives them, a declared `@mcp` shaping only its hints;
           each call acts as whoever called, when the bus is guarded.
        3. Final: a FastMCP 3 instance ready to run (stdio by default) — with `auth`, FastMCP
           verifies bearer tokens itself, and with `base_url` publishes the resource metadata.
        """
        try:
            from fastmcp import FastMCP  # pyright: ignore[reportMissingImports]
        except ImportError as error:
            raise ImportError(FASTMCP_MISSING) from error

        verifier = token_verifier(auth, base_url) if auth is not None else None
        mcp = FastMCP(name or self.name, auth=verifier)
        bus = self.catalog.framework_instance
        for operation in self.catalog.get_scalar_use_cases(filter_binaries_schema=True):
            mcp.tool(
                fastmcp_callable(operation, bus),
                name=operation.name,
                description=operation.description,
                tags={operation.layer},
                annotations=_catalog_hints(operation),
            )
        return mcp

    def run(self, name: str | None = None, **kwargs: Any) -> None:
        """Start the MCP host. Same catalog; transport is the only difference.

        Omit transport → stdio (Cursor, Claude Desktop, CLI).
        transport='http' → MCP Streamable HTTP at /mcp — not a REST/OpenAPI API.
        Extra kwargs go to FastMCP.run (host, port, path, ...).
        """
        self.server(name=name).run(**kwargs)


class McpGateway(Gateway):
    """MCP over one or more bounded contexts — the declared surface, each use case a tool.

        McpGateway([billing, sales]).server()                         # what is bound: @mcp()
        McpGateway([billing, sales], exposure=Exposure.CATALOG).server()   # every use case

    Context: `Exposure.DECLARED` (the default) publishes only the use cases bound with `@mcp`
    or `bind(Command, McpBinding(...))`; `Exposure.CATALOG` publishes every one and logs each.
    A tool is named by its DTO (`issue_invoice`), prefixed by its group only when two contexts
    answer the same name; its hints are derived (`McpWire`). Each call acts as whoever called,
    authenticated and authorized by its bus's `AccessControl` — a hint never authorizes.
    `auth=` adds FastMCP's own bearer check; `port=` a `McpWire` of the project's.
    """

    wire = "mcp"

    def __init__(
        self,
        instances: Buses | None = None,
        layers: Iterable[str] = DEFAULT_LAYERS,
        title: str = "sincpro-mcp",
        version: str = "1.0.0",
        exposure: Exposure = Exposure.DECLARED,
        unguarded: bool = False,
        port: McpWire | None = None,
    ):
        super().__init__(
            instances,
            layers,
            title,
            version,
            exposure=exposure,
            unguarded=unguarded,
            port=port or McpWire(),
        )

    def tools(self) -> list[McpTool]:
        """The validated surface as tools — refused with `ProgrammingError`, every reason at
        once, when it does not hold."""
        return self.build()

    def tool_names(self) -> list[str]:
        return [tool.name for tool in self.tools()]

    def server(
        self,
        name: str | None = None,
        auth: "AccessControl[Any] | None" = None,
        base_url: str | None = None,
    ) -> Any:
        """A FastMCP 3 server with every tool of the surface — returned, not run, so the project
        adds its middleware, prompts or resources to it."""
        try:
            from fastmcp import FastMCP  # pyright: ignore[reportMissingImports]
        except ImportError as error:
            raise ImportError(FASTMCP_MISSING) from error

        tools = self.tools()
        verifier = token_verifier(auth, base_url) if auth is not None else None
        mcp = FastMCP(name or self._title, auth=verifier)
        for tool in tools:
            operation = tool.operation
            mcp.tool(
                tool_function(
                    operation.command,
                    tool.name,
                    tool.description,
                    operation.run,
                    operation.bus,
                ),
                name=tool.name,
                title=tool.title,
                description=tool.description,
                tags={str(operation.layer)},
                annotations=tool.annotations,
            )
        return mcp

    def run(self, name: str | None = None, **kwargs: Any) -> None:
        self.server(name=name).run(**kwargs)


def build_mcp_server(
    framework_instance: UseFramework | Buses,
    name: str | None = None,
    auth: "AccessControl[Any] | None" = None,
    base_url: str | None = None,
) -> Any:
    """The whole catalog as tools, with no declaration — the convenience for an SDK or an MCP
    server over a whole context (PRD_14 §2, CATALOG).

    1. One bus: `Entrypoint` — PRD_12's catalog, each tool named by its DTO class, as always.
    2. Several: `McpGateway` in `Exposure.CATALOG`, its buses taken as unguarded on purpose when
       they have no `AccessControl` — a guarded one still needs every use case to say who may
       call it.
    3. Final: a FastMCP server, not run.
    """
    if isinstance(framework_instance, UseFramework):
        return Entrypoint(framework_instance).server(name=name, auth=auth, base_url=base_url)
    return McpGateway(framework_instance, exposure=Exposure.CATALOG, unguarded=True).server(
        name=name, auth=auth, base_url=base_url
    )
