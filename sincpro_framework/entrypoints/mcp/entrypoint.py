"""MCP entrypoint: orchestrates the shared catalog and the FastMCP wire (mcp.py)."""

from collections import Counter
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any, Self

from sincpro_framework.entrypoints.catalog import Catalog, PackedFeatureOrAppService
from sincpro_framework.entrypoints.gateway import DEFAULT_LAYERS, Buses, Gateway
from sincpro_framework.entrypoints.mcp.auth import token_verifier
from sincpro_framework.entrypoints.mcp.mcp import FASTMCP_MISSING, fastmcp_callable
from sincpro_framework.use_bus import UseFramework

if TYPE_CHECKING:
    from sincpro_framework.auth.access_control import AccessControl


class Entrypoint:
    """MCP facade over one UseFramework instance."""

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
           with mcp.tool(fn, name=..., description=..., tags=layer); each call acts as whoever
           called, when the bus is guarded.
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
    """MCP facade over one or more UseFramework instances — every published use case a tool.

        McpGateway([billing, sales]).server()             # automatic: every bus, every use case

    Context: a tool is named by its DTO; a name two buses answer is qualified by the alias —
    `billing_CommandIssueInvoice` — so no tool shadows another. Each call acts as whoever called,
    authenticated by its bus's `AccessControl`; `auth=` adds FastMCP's own bearer check.
    """

    def __init__(
        self,
        instances: Buses | None = None,
        layers: Iterable[str] = DEFAULT_LAYERS,
        title: str = "sincpro-mcp",
        version: str = "1.0.0",
    ):
        super().__init__(instances, layers, title, version)

    def _named(self) -> list[tuple[str, UseFramework, PackedFeatureOrAppService]]:
        listed = self.operations()
        counted = Counter(operation.name for _, _, operation in listed)
        return [
            (
                (
                    f"{alias}_{operation.name}"
                    if counted[operation.name] > 1
                    else operation.name
                ),
                bus,
                operation,
            )
            for alias, bus, operation in listed
        ]

    def tool_names(self) -> list[str]:
        return [name for name, _, _ in self._named()]

    def server(
        self,
        name: str | None = None,
        auth: "AccessControl[Any] | None" = None,
        base_url: str | None = None,
    ) -> Any:
        """A FastMCP 3 server with every published use case as a tool — returned, not run, so
        the project adds its middleware, prompts or resources to it."""
        try:
            from fastmcp import FastMCP  # pyright: ignore[reportMissingImports]
        except ImportError as error:
            raise ImportError(FASTMCP_MISSING) from error

        verifier = token_verifier(auth, base_url) if auth is not None else None
        mcp = FastMCP(name or self._title, auth=verifier)
        for tool_name, bus, operation in self._named():
            mcp.tool(
                fastmcp_callable(operation, bus),
                name=tool_name,
                description=operation.description,
                tags={operation.layer},
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
    """One bus — its tools named by their DTOs, as always — or several, through `McpGateway`."""
    if isinstance(framework_instance, UseFramework):
        return Entrypoint(framework_instance).server(name=name, auth=auth, base_url=base_url)
    return McpGateway(framework_instance).server(name=name, auth=auth, base_url=base_url)
