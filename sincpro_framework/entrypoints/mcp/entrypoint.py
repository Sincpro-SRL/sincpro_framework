"""MCP entrypoint: wrap a UseFramework instance as an MCP server."""

import inspect
from collections.abc import Callable
from typing import Any, Self

from pydantic_core import PydanticUndefined

from sincpro_framework.entrypoints.mcp.tools import (
    Tool,
    collect_tools,
    dto_is_json_serializable,
)
from sincpro_framework.sincpro_logger import logger
from sincpro_framework.use_bus import UseFramework

FASTMCP_MISSING = "FastMCP is not installed. Install with: pip install sincpro-framework[mcp]"


def fastmcp_callable(tool: Tool):
    """Build a typed function FastMCP 3 inspects to generate the MCP schema.

    1. Forward keyword arguments to tool.run (DTO.model_validate inside).
    2. Stamp a keyword-only signature from DTO fields so FastMCP sees Pydantic types
       (Value Objects, Field descriptions) instead of a nested wrapper object.
        2.1 Required fields have no default.
        2.2 Optional fields keep the Pydantic default.
    3. Stamp name and docstring from the Tool (FastMCP infers tool name / description).
    4. Final: a function for mcp.tool(fn, name=..., description=...).
    """

    def tool_fn(**kwargs: Any) -> dict[str, Any]:
        return tool.run(kwargs)

    parameters: list[inspect.Parameter] = []
    annotations: dict[str, Any] = {"return": dict[str, Any]}
    for field_name, field_info in tool.dto.model_fields.items():
        annotations[field_name] = field_info.annotation
        if field_info.is_required() or field_info.default is PydanticUndefined:
            default: Any = inspect.Parameter.empty
        else:
            default = field_info.default
        parameters.append(
            inspect.Parameter(
                field_name,
                inspect.Parameter.KEYWORD_ONLY,
                default=default,
                annotation=field_info.annotation,
            )
        )
    tool_fn.__name__ = tool.name
    tool_fn.__doc__ = tool.description
    tool_fn.__annotations__ = annotations
    setattr(tool_fn, "__signature__", inspect.Signature(parameters))
    return tool_fn


class Entrypoint:
    """MCP facade over one UseFramework instance."""

    def __init__(self, framework: UseFramework):
        if not framework.was_initialized:
            framework.build_root_bus()
        self.framework = framework
        self._include: set[str] | None = None
        self._exclude: set[str] = set()
        self._wrappers: dict[str, Callable] = {}

    @property
    def name(self) -> str:
        return self.framework._logger_name

    def include(self, *dtos: type | str) -> Self:
        self._include = {dto if isinstance(dto, str) else dto.__name__ for dto in dtos}
        return self

    def exclude(self, *dtos: type | str) -> Self:
        self._exclude = {dto if isinstance(dto, str) else dto.__name__ for dto in dtos}
        return self

    def wrap(self, dto: type | str, wrapper: Callable) -> Self:
        key = dto if isinstance(dto, str) else dto.__name__
        self._wrappers[key] = wrapper
        return self

    def tools(self) -> list[Tool]:
        return collect_tools(
            self.framework,
            include=self._include,
            exclude=self._exclude,
            wrappers=self._wrappers,
        )

    def to_callables(self) -> dict[str, Callable[[dict[str, Any]], dict[str, Any]]]:
        return {tool.name: tool.run for tool in self.tools()}

    def server(self, name: str | None = None) -> Any:
        """Publish JSON-safe tools on a FastMCP server.

        1. Import FastMCP or raise with the extra-install hint.
        2. For each collected tool, skip DTOs that are not JSON-serializable.
        3. Register the rest with mcp.tool(fn, name=..., description=..., tags=kind).
        4. Final: a FastMCP 3 instance ready to run (stdio by default).
        """
        try:
            from fastmcp import FastMCP  # pyright: ignore[reportMissingImports]
        except ImportError as error:
            raise ImportError(FASTMCP_MISSING) from error

        mcp = FastMCP(name or self.name)
        for tool in self.tools():
            if not dto_is_json_serializable(tool.dto):
                logger.warning("Skipping MCP tool [%s]: DTO is not JSON", tool.name)
                continue
            mcp.tool(
                fastmcp_callable(tool),
                name=tool.name,
                description=tool.description,
                tags={tool.kind},
            )
        return mcp

    def run(self, name: str | None = None, **kwargs: Any) -> None:
        """Start the MCP host. Same catalog; transport is the only difference.

        Omit transport → stdio (Cursor, Claude Desktop, CLI).
        transport='http' → MCP Streamable HTTP at /mcp — not a REST/OpenAPI API.
        Extra kwargs go to FastMCP.run (host, port, path, ...).
        """
        self.server(name=name).run(**kwargs)


def build_mcp_server(framework: UseFramework, name: str | None = None) -> Any:
    return Entrypoint(framework).server(name=name)
