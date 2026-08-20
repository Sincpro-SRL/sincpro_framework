"""Generate MCP tools from a UseFramework bus."""

import inspect
import json
from collections.abc import Callable
from typing import Any, get_args, get_origin

from pydantic import BaseModel

from sincpro_framework.sincpro_abstractions import DataTransferObject
from sincpro_framework.sincpro_logger import logger
from sincpro_framework.use_bus import UseFramework

BINARY_TYPES = (bytes, bytearray, memoryview)
BINARY_JSON_FORMATS = {"binary", "byte"}


class Tool(DataTransferObject):
    """Projection of one Feature or ApplicationService as an MCP tool.

    `run` is a bound callable, not JSON. DataTransferObject allows arbitrary types.
    """

    name: str
    kind: str
    description: str
    dto: type[DataTransferObject]
    json_schema: dict[str, Any]
    run: Callable[[dict[str, Any]], dict[str, Any]]


def class_own_docstring(cls: type) -> str | None:
    """Docstring declared on this class, not inherited from Feature/ApplicationService."""
    raw = cls.__dict__.get("__doc__")
    if raw and str(raw).strip():
        return inspect.cleandoc(raw)
    return None


def tool_instruction(handler_class: type, dto_type: type, dto_name: str) -> str:
    """Pick the MCP description without inheriting the Feature/ApplicationService base essay.

    1. Prefer the handler class's own docstring.
    2. Else the execute method's own docstring.
    3. Else the input DTO's own docstring.
    4. Final: the DTO class name.
    """
    execute = handler_class.__dict__.get("execute")
    execute_doc = (
        inspect.cleandoc(execute.__doc__) if execute is not None and execute.__doc__ else None
    )
    return (
        class_own_docstring(handler_class)
        or execute_doc
        or class_own_docstring(dto_type)
        or dto_name
    )


def dto_json_schema(dto_type: type[DataTransferObject]) -> dict[str, Any]:
    try:
        return dto_type.model_json_schema()
    except Exception:
        return {"type": "object", "title": dto_type.__name__}


def json_safe_dict(payload: dict[str, Any], source: str) -> dict[str, Any]:
    """Guarantee the MCP host can serialize the payload.

    Pydantic keeps arbitrary objects (a Zeep SOAP response on an ``Any`` field) in
    the dump instead of raising, so the failure would otherwise surface inside the
    MCP host, after the Feature already ran its side effect.

    1. Return the payload untouched when json.dumps accepts it.
    2. Else stringify the offending leaves and warn naming the response.
    3. Final: a dict json.dumps accepts, with the JSON-safe keys intact.
    """
    try:
        json.dumps(payload)
        return payload
    except (TypeError, ValueError):
        logger.warning("Non-JSON value in [%s] response: coerced to string", source)
        return json.loads(json.dumps(payload, default=str))


def dump_json_result(result: Any) -> dict[str, Any]:
    """Turn a bus response into a JSON object for MCP.

    1. None becomes {}.
    2. A Pydantic model dumps in JSON mode; fall back to Python dump if that fails.
    3. A dict passes through; any other value is wrapped as {result: value}.
    4. Coerce whatever is left into a JSON-serializable dict.
    5. Final: a dict FastMCP can serialize.
    """
    if result is None:
        return {}
    if isinstance(result, BaseModel):
        try:
            dumped = result.model_dump(mode="json")
        except Exception:
            dumped = result.model_dump()
        payload = dumped if isinstance(dumped, dict) else {"result": dumped}
    elif isinstance(result, dict):
        payload = result
    else:
        payload = {"result": result}
    return json_safe_dict(payload, type(result).__name__)


def is_binary_annotation(annotation: Any) -> bool:
    if annotation in BINARY_TYPES:
        return True
    origin = get_origin(annotation)
    if origin is None:
        return False
    return any(is_binary_annotation(arg) for arg in get_args(annotation))


def json_schema_has_binary(schema: dict[str, Any], seen: set[int] | None = None) -> bool:
    """Walk a Pydantic JSON Schema tree looking for binary payloads.

    1. Skip nodes already visited (cyclic $defs).
    2. Treat format binary/byte as binary.
    3. Recurse into properties, $defs, items, additionalProperties, anyOf/oneOf/allOf.
    4. Final: True if any node is binary, else False.
    """
    seen = seen if seen is not None else set()
    if id(schema) in seen:
        return False
    seen.add(id(schema))
    if schema.get("format") in BINARY_JSON_FORMATS:
        return True
    for key in ("properties", "$defs", "definitions"):
        nested = schema.get(key)
        if isinstance(nested, dict):
            for value in nested.values():
                if isinstance(value, dict) and json_schema_has_binary(value, seen):
                    return True
    for key in ("items", "additionalProperties"):
        nested = schema.get(key)
        if isinstance(nested, dict) and json_schema_has_binary(nested, seen):
            return True
    for key in ("anyOf", "oneOf", "allOf"):
        for item in schema.get(key, []):
            if isinstance(item, dict) and json_schema_has_binary(item, seen):
                return True
    return False


def dto_is_json_serializable(dto_type: type[DataTransferObject]) -> bool:
    """Decide whether a DTO can be an MCP tool input.

    1. Fail if model_json_schema cannot be built.
    2. Fail if the schema tree contains binary formats.
    3. Fail if any field annotation is bytes-like.
    4. Final: True only when schema and annotations are JSON-safe.
    """
    try:
        schema = dto_type.model_json_schema()
    except Exception:
        return False
    if json_schema_has_binary(schema):
        return False
    return not any(
        is_binary_annotation(field.annotation) for field in dto_type.model_fields.values()
    )


def bind_framework_execute(
    framework: UseFramework, dto_type: type[DataTransferObject]
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Close over the DTO so MCP JSON becomes a bus call.

    1. Validate the payload as the DTO (Value Objects run here).
    2. Execute through UseFramework.
    3. Final: dump the response as a JSON object.
    """

    def run(payload: dict[str, Any]) -> dict[str, Any]:
        result = framework(dto_type.model_validate(payload))
        return dump_json_result(result)

    return run


def tools_from_bus_registry(
    framework: UseFramework,
    registry: dict[str, Any],
    dto_registry: dict[str, type[DataTransferObject]],
    kind: str,
    include: set[str] | None,
    exclude: set[str],
    wrappers: dict[str, Callable],
) -> list[Tool]:
    """Project one bus registry into Tool DTOs.

    1. Skip names outside include, or listed in exclude.
    2. Skip registry keys with no DTO class.
    3. Bind execute to framework(dto).
        3.1 If a wrapper exists for this DTO name, wrap the bound run.
    4. Final: a Tool whose description is the handler docstring and whose schema is the DTO.
    """
    tools: list[Tool] = []
    for dto_name, handler in registry.items():
        if include is not None and dto_name not in include:
            continue
        if dto_name in exclude:
            continue
        dto_type = dto_registry.get(dto_name)
        if dto_type is None:
            continue
        run = bind_framework_execute(framework, dto_type)
        wrapper = wrappers.get(dto_name)
        if wrapper is not None:
            run = wrapper(run)
        tools.append(
            Tool(
                name=dto_name,
                kind=kind,
                description=tool_instruction(handler.__class__, dto_type, dto_name),
                dto=dto_type,
                json_schema=dto_json_schema(dto_type),
                run=run,
            )
        )
    return tools


def collect_tools(
    framework: UseFramework,
    include: set[str] | None = None,
    exclude: set[str] | None = None,
    wrappers: dict[str, Callable] | None = None,
) -> list[Tool]:
    """Walk the framework bus and project Features and ApplicationServices as tools.

    1. Build the root bus if the instance was never initialized.
    2. If the bus is missing, return an empty list.
    3. Collect tools from the Feature registry.
    4. Collect tools from the ApplicationService registry.
    5. Final: Features then ApplicationServices, already filtered by include/exclude.
    """
    if not framework.was_initialized:
        framework.build_root_bus()
    bus = framework.bus
    if bus is None:
        return []

    skip = exclude or set()
    wraps = wrappers or {}
    tools: list[Tool] = []
    tools.extend(
        tools_from_bus_registry(
            framework,
            bus.feature_bus.feature_registry,
            bus.dto_registry,
            "feature",
            include,
            skip,
            wraps,
        )
    )
    tools.extend(
        tools_from_bus_registry(
            framework,
            bus.app_service_bus.app_service_registry,
            bus.dto_registry,
            "application_service",
            include,
            skip,
            wraps,
        )
    )
    return tools
