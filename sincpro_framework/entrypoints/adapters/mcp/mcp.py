"""FastMCP binding: a use case → typed function FastMCP 3 can register as a tool."""

import dataclasses
import inspect
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Annotated, Any

from pydantic import BaseModel, Field

from sincpro_framework.context.domain.level import EntrypointKind
from sincpro_framework.entrypoints.entrypoint.catalog import PackedFeatureOrAppService

if TYPE_CHECKING:
    from sincpro_framework.use_bus import UseFramework

EMPTY = inspect.Parameter.empty

FASTMCP_MISSING = "FastMCP is not installed. Install with: pip install sincpro-framework[mcp]"


def _dto_parameters(
    dto_type: Any,
) -> Iterator[tuple[str, Any, Any, Callable[..., Any] | None]]:
    """`(name, annotation, default, default_factory)` per field of a Command.

    A Command is a DataTransferObject almost always, and a dataclass when a
    context maps its domain imperatively — the bus's `TypeDTO` admits both, so
    the tool signature is built from whichever this one is.
    """
    if isinstance(dto_type, type) and issubclass(dto_type, BaseModel):
        for name, field_info in dto_type.model_fields.items():
            default = EMPTY if field_info.is_required() else field_info.default
            yield name, field_info.annotation, default, field_info.default_factory
        return
    if not dataclasses.is_dataclass(dto_type):
        return
    for field in dataclasses.fields(dto_type):
        factory = (
            field.default_factory
            if field.default_factory is not dataclasses.MISSING
            else None
        )
        default = field.default if field.default is not dataclasses.MISSING else EMPTY
        yield field.name, field.type, default, factory


def fastmcp_callable(operation: PackedFeatureOrAppService, bus: "UseFramework | None" = None):
    """A catalog entry as a function FastMCP registers — `tool_function` over its DTO, name,
    description and bound run."""
    return tool_function(
        operation.dto, operation.name, operation.description, operation.run, bus
    )


def tool_function(
    dto: Any,
    name: str,
    description: str,
    run: Callable[[dict[str, Any]], Any],
    bus: "UseFramework | None" = None,
) -> Callable[..., Any]:
    """Build a typed function FastMCP 3 inspects to generate the MCP schema.

    1. Forward keyword arguments to `run` — the bus, DTO validation inside — acting as whoever
       called, authenticated by `bus`'s `AccessControl` when a bus is given.
    2. Stamp a keyword-only signature from the Command's fields so FastMCP sees
       Pydantic types (Value Objects, Field descriptions) instead of a nested
       wrapper object.
        2.1 Required fields have no default.
        2.2 A default_factory travels as Annotated metadata, never as a value: a
            concrete default would freeze uuid4/datetime.now at import time.
        2.3 Any other optional field keeps its declared default.
    3. Stamp name and docstring (FastMCP infers tool name / description).
    4. Final: a function for mcp.tool(fn, name=..., description=...).
    """

    def tool_fn(**kwargs: Any) -> dict[str, Any]:
        from sincpro_framework.entrypoints.adapters.mcp.auth import acting_for_tool_call

        with acting_for_tool_call(bus):
            if bus is None:
                return run(kwargs)
            with bus.context(kind=EntrypointKind.MCP):
                return run(kwargs)

    parameters: list[inspect.Parameter] = []
    annotations: dict[str, Any] = {"return": dict[str, Any]}
    for field_name, annotation, default, default_factory in _dto_parameters(dto):
        if default_factory is not None:
            annotation = Annotated[annotation, Field(default_factory=default_factory)]
            default = EMPTY
        annotations[field_name] = annotation
        parameters.append(
            inspect.Parameter(
                field_name,
                inspect.Parameter.KEYWORD_ONLY,
                default=default,
                annotation=annotation,
            )
        )
    tool_fn.__name__ = name
    tool_fn.__doc__ = description
    tool_fn.__annotations__ = annotations
    setattr(tool_fn, "__signature__", inspect.Signature(parameters))
    return tool_fn
