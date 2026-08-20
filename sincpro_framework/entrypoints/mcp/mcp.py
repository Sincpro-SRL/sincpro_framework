"""FastMCP wire: PackedFeatureOrAppService → typed function FastMCP 3 can register as a tool."""

import inspect
from typing import Annotated, Any

from pydantic import Field

from sincpro_framework.entrypoints.catalog import PackedFeatureOrAppService

FASTMCP_MISSING = "FastMCP is not installed. Install with: pip install sincpro-framework[mcp]"


def fastmcp_callable(operation: PackedFeatureOrAppService):
    """Build a typed function FastMCP 3 inspects to generate the MCP schema.

    1. Forward keyword arguments to operation.run (DTO.model_validate inside).
    2. Stamp a keyword-only signature from DTO fields so FastMCP sees Pydantic types
       (Value Objects, Field descriptions) instead of a nested wrapper object.
        2.1 Required fields have no default.
        2.2 A default_factory travels as Annotated metadata, never as a value: a
            concrete default would freeze uuid4/datetime.now at import time.
        2.3 Any other optional field keeps the Pydantic default.
    3. Stamp name and docstring from the PackedFeatureOrAppService (FastMCP infers tool name / description).
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
