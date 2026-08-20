"""MCP entrypoint: wrap a UseFramework instance as an MCP server."""

import inspect
from collections.abc import Callable
from typing import Annotated, Any, Self

from pydantic import Field

from sincpro_framework.entrypoints.catalog import Catalog, Operation
from sincpro_framework.use_bus import UseFramework

FASTMCP_MISSING = "FastMCP is not installed. Install with: pip install sincpro-framework[mcp]"


def fastmcp_callable(operation: Operation):
    """Build a typed function FastMCP 3 inspects to generate the MCP schema.

    1. Forward keyword arguments to operation.run (DTO.model_validate inside).
    2. Stamp a keyword-only signature from DTO fields so FastMCP sees Pydantic types
       (Value Objects, Field descriptions) instead of a nested wrapper object.
        2.1 Required fields have no default.
        2.2 A default_factory travels as Annotated metadata, never as a value: a
            concrete default would freeze uuid4/datetime.now at import time.
        2.3 Any other optional field keeps the Pydantic default.
    3. Stamp name and docstring from the Operation (FastMCP infers tool name / description).
    4. Final: a function for mcp.tool(fn, name=..., description=...).
    """

    def tool_fn(**kwargs: Any) -> dict[str, Any]:
        return operation.run(kwargs)

    parameters: list[inspect.Parameter] = []
    annotations: dict[str, Any] = {"return": dict[str, Any]}
    for field_name, field_info in operation.dto.model_fields.items():
        annotation = field_info.annotation
        default: Any = inspect.Parameter.empty
        if field_info.default_factory is not None:
            annotation = Annotated[
                annotation, Field(default_factory=field_info.default_factory)
            ]
        elif not field_info.is_required():
            default = field_info.default
        annotations[field_name] = annotation
        parameters.append(
            inspect.Parameter(
                field_name,
                inspect.Parameter.KEYWORD_ONLY,
                default=default,
                annotation=annotation,
            )
        )
    tool_fn.__name__ = operation.name
    tool_fn.__doc__ = operation.description
    tool_fn.__annotations__ = annotations
    setattr(tool_fn, "__signature__", inspect.Signature(parameters))
    return tool_fn


class Entrypoint:
    """MCP facade over one UseFramework instance."""

    def __init__(self, framework: UseFramework):
        self.catalog = Catalog(framework)

    @property
    def name(self) -> str:
        return self.catalog.framework._logger_name

    def include(self, *dtos: type | str) -> Self:
        self.catalog.include(*dtos)
        return self

    def exclude(self, *dtos: type | str) -> Self:
        self.catalog.exclude(*dtos)
        return self

    def wrap(self, dto: type | str, wrapper: Callable) -> Self:
        self.catalog.wrap(dto, wrapper)
        return self

    def tools(self) -> list[Operation]:
        return self.catalog.operations()

    def to_callables(self) -> dict[str, Callable[[dict[str, Any]], dict[str, Any]]]:
        return {operation.name: operation.run for operation in self.catalog.operations()}

    def server(self, name: str | None = None) -> Any:
        """Publish JSON-safe operations on a FastMCP server.

        1. Import FastMCP or raise with the extra-install hint.
        2. Register catalog.published() with mcp.tool(fn, name=..., description=..., tags=kind).
        3. Final: a FastMCP 3 instance ready to run (stdio by default).
        """
        try:
            from fastmcp import FastMCP  # pyright: ignore[reportMissingImports]
        except ImportError as error:
            raise ImportError(FASTMCP_MISSING) from error

        mcp = FastMCP(name or self.name)
        for operation in self.catalog.published():
            mcp.tool(
                fastmcp_callable(operation),
                name=operation.name,
                description=operation.description,
                tags={operation.kind},
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
