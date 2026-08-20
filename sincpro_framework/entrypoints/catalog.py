"""Shared catalog for driving adapters (MCP, JSON-RPC, later REST/CLI).

Contract (hosts depend on this; this does not depend on hosts):

- Operation: one Feature or ApplicationService as JSON in → bus → JSON out.
- Catalog: include / exclude / wrap on one UseFramework instance.
- published(): JSON-safe subset (bytes DTOs stay Python-only).
- invoke(): optional framework.context + with_trace around run().
- layer_for_kind(): feature → features, application_service → app_services.

Hosts only add a wire: FastMCP tools, JSON-RPC methods, FastAPI routes, CLI commands.
Do not put FastMCP, Starlette, or argparse types here.
"""

import inspect
import json
from collections.abc import Callable, Mapping
from typing import Any, Literal, Self, get_args, get_origin

from pydantic import BaseModel

from sincpro_framework.sincpro_abstractions import DataTransferObject
from sincpro_framework.sincpro_logger import logger
from sincpro_framework.use_bus import UseFramework

BINARY_TYPES = (bytes, bytearray, memoryview)
BINARY_JSON_FORMATS = {"binary", "byte"}
KIND_FEATURE: Literal["feature"] = "feature"
KIND_APP_SERVICE: Literal["application_service"] = "application_service"
LAYER_FEATURES = "features"
LAYER_APP_SERVICES = "app_services"
LAYER_BY_KIND = {
    KIND_FEATURE: LAYER_FEATURES,
    KIND_APP_SERVICE: LAYER_APP_SERVICES,
}
TRACE_KEYS = ("trace_id", "span_id", "carrier")
RunFn = Callable[[dict[str, Any]], dict[str, Any]]
Wrapper = Callable[[RunFn], RunFn]


class Operation(DataTransferObject):
    """Projection of one Feature or ApplicationService as a gateway operation.

    `run` is a bound callable, not JSON. DataTransferObject allows arbitrary types.
    """

    name: str
    kind: str
    description: str
    dto: type[DataTransferObject]
    json_schema: dict[str, Any]
    run: RunFn


Tool = Operation


def layer_for_kind(kind: str) -> str:
    return LAYER_BY_KIND[kind]


def class_own_docstring(cls: type) -> str | None:
    """Docstring declared on this class, not inherited from Feature/ApplicationService."""
    raw = cls.__dict__.get("__doc__")
    if raw and str(raw).strip():
        return inspect.cleandoc(raw)
    return None


def operation_instruction(handler_class: type, dto_type: type, dto_name: str) -> str:
    """Pick the operation description without inheriting the Feature/ApplicationService base essay.

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


tool_instruction = operation_instruction


def dto_json_schema(dto_type: type[DataTransferObject]) -> dict[str, Any]:
    try:
        return dto_type.model_json_schema()
    except Exception:
        return {"type": "object", "title": dto_type.__name__}


def json_safe_dict(payload: dict[str, Any], source: str) -> dict[str, Any]:
    """Guarantee a driving adapter can serialize the payload.

    Pydantic keeps arbitrary objects (a Zeep SOAP response on an ``Any`` field) in
    the dump instead of raising, so the failure would otherwise surface inside the
    host, after the Feature already ran its side effect.

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
    """Turn a bus response into a JSON object.

    1. None becomes {}.
    2. A Pydantic model dumps in JSON mode; fall back to Python dump if that fails.
    3. A dict passes through; any other value is wrapped as {result: value}.
    4. Coerce whatever is left into a JSON-serializable dict.
    5. Final: a dict a JSON host can serialize.
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
    """Decide whether a DTO can travel as JSON into a gateway.

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
) -> RunFn:
    """Close over the DTO so JSON becomes a bus call.

    1. Validate the payload as the DTO (Value Objects run here).
    2. Execute through UseFramework.
    3. Final: dump the response as a JSON object.
    """

    def run(payload: dict[str, Any]) -> dict[str, Any]:
        result = framework(dto_type.model_validate(payload))
        return dump_json_result(result)

    return run


def invoke(
    framework: UseFramework,
    run: RunFn,
    payload: dict[str, Any],
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one catalog operation inside framework.context / with_trace when asked.

    1. Split tracing keys (trace_id, span_id, carrier) from the rest of the context.
    2. Enter framework.context with the remaining keys when any remain.
    3. Enter framework.with_trace when tracing keys are present.
    4. Final: run(payload) sees self.context and the OTel parent when configured.
    """
    extra = dict(context or {})
    trace_kwargs: dict[str, Any] = {}
    for key in TRACE_KEYS:
        if key not in extra:
            continue
        value = extra.pop(key)
        if value is not None:
            trace_kwargs[key] = value

    if extra and trace_kwargs:
        with framework.context(extra):
            with framework.with_trace(**trace_kwargs):
                return run(payload)
    if extra:
        with framework.context(extra):
            return run(payload)
    if trace_kwargs:
        with framework.with_trace(**trace_kwargs):
            return run(payload)
    return run(payload)


def operations_from_bus_registry(
    framework: UseFramework,
    registry: dict[str, Any],
    dto_registry: dict[str, type[DataTransferObject]],
    kind: str,
    include: set[str] | None,
    exclude: set[str],
    wrappers: dict[str, Wrapper],
) -> list[Operation]:
    """Project one bus registry into Operation DTOs.

    1. Skip names outside include, or listed in exclude.
    2. Skip registry keys with no DTO class.
    3. Bind execute to framework(dto).
        3.1 If a wrapper exists for this DTO name, wrap the bound run.
    4. Final: an Operation whose description is the handler docstring and whose schema is the DTO.
    """
    operations: list[Operation] = []
    for dto_name, handler in registry.items():
        if include is not None and dto_name not in include:
            continue
        if dto_name in exclude:
            continue
        dto_type = dto_registry.get(dto_name)
        if dto_type is None:
            continue
        run: RunFn = bind_framework_execute(framework, dto_type)
        wrapper = wrappers.get(dto_name)
        if wrapper is not None:
            run = wrapper(run)
        operations.append(
            Operation(
                name=dto_name,
                kind=kind,
                description=operation_instruction(handler.__class__, dto_type, dto_name),
                dto=dto_type,
                json_schema=dto_json_schema(dto_type),
                run=run,
            )
        )
    return operations


def collect_operations(
    framework: UseFramework,
    include: set[str] | None = None,
    exclude: set[str] | None = None,
    wrappers: dict[str, Wrapper] | None = None,
) -> list[Operation]:
    """Walk the framework bus and project Features and ApplicationServices.

    1. Build the root bus if the instance was never initialized.
    2. If the bus is missing, return an empty list.
    3. Collect operations from the Feature registry.
    4. Collect operations from the ApplicationService registry.
    5. Final: Features then ApplicationServices, already filtered by include/exclude.
    """
    if not framework.was_initialized:
        framework.build_root_bus()
    bus = framework.bus
    if bus is None:
        return []

    skip = exclude or set()
    wraps = wrappers or {}
    operations: list[Operation] = []
    operations.extend(
        operations_from_bus_registry(
            framework,
            bus.feature_bus.feature_registry,
            bus.dto_registry,
            KIND_FEATURE,
            include,
            skip,
            wraps,
        )
    )
    operations.extend(
        operations_from_bus_registry(
            framework,
            bus.app_service_bus.app_service_registry,
            bus.dto_registry,
            KIND_APP_SERVICE,
            include,
            skip,
            wraps,
        )
    )
    return operations


collect_tools = collect_operations
tools_from_bus_registry = operations_from_bus_registry


def json_safe_operations(operations: list[Operation]) -> list[Operation]:
    """Drop operations whose input DTO cannot travel as JSON."""
    published: list[Operation] = []
    for operation in operations:
        if dto_is_json_serializable(operation.dto):
            published.append(operation)
            continue
        logger.warning("Skipping non-JSON operation [%s]", operation.name)
    return published


def _dto_names(*dtos: type | str) -> set[str]:
    return {dto if isinstance(dto, str) else dto.__name__ for dto in dtos}


class Catalog:
    """One UseFramework instance as a filtered list of operations.

    Hosts (MCP, JSON-RPC, REST, CLI) take published() and map it to their wire.
    """

    def __init__(self, framework: UseFramework):
        if not framework.was_initialized:
            framework.build_root_bus()
        self.framework = framework
        self._include: set[str] | None = None
        self._exclude: set[str] = set()
        self._wrappers: dict[str, Wrapper] = {}

    def include(self, *dtos: type | str) -> Self:
        self._include = _dto_names(*dtos)
        return self

    def exclude(self, *dtos: type | str) -> Self:
        self._exclude = _dto_names(*dtos)
        return self

    def wrap(self, dto: type | str, wrapper: Wrapper) -> Self:
        key = dto if isinstance(dto, str) else dto.__name__
        self._wrappers[key] = wrapper
        return self

    def operations(self) -> list[Operation]:
        return collect_operations(
            self.framework,
            include=self._include,
            exclude=self._exclude,
            wrappers=self._wrappers,
        )

    def published(self) -> list[Operation]:
        """Operations a JSON host may advertise (binary DTOs omitted)."""
        return json_safe_operations(self.operations())
