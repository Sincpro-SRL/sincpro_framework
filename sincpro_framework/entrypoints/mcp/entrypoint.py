"""MCP entrypoint: orchestrates the shared catalog and the FastMCP wire (mcp.py)."""

from collections.abc import Callable
from typing import Any, Self

from sincpro_framework.entrypoints.catalog import Catalog, PackedFeatureOrAppService
from sincpro_framework.entrypoints.mcp.mcp import FASTMCP_MISSING, fastmcp_callable
from sincpro_framework.use_bus import UseFramework


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

    def server(self, name: str | None = None) -> Any:
        """Publish JSON-safe Features/ApplicationServices on a FastMCP server.

        1. Import FastMCP or raise with the extra-install hint.
        2. Register catalog.build_scalar_feature_and_app_services(filter_binaries_schema=True)
           with mcp.tool(fn, name=..., description=..., tags=layer).
        3. Final: a FastMCP 3 instance ready to run (stdio by default).
        """
        try:
            from fastmcp import FastMCP  # pyright: ignore[reportMissingImports]
        except ImportError as error:
            raise ImportError(FASTMCP_MISSING) from error

        mcp = FastMCP(name or self.name)
        for operation in self.catalog.get_scalar_use_cases(filter_binaries_schema=True):
            mcp.tool(
                fastmcp_callable(operation),
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


def build_mcp_server(framework_instance: UseFramework, name: str | None = None) -> Any:
    return Entrypoint(framework_instance).server(name=name)
