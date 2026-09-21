"""Decide whether a DTO can travel as JSON, and build its JSON Schema.

Pydantic's model_json_schema() stores a nested submodel's definition once
under $defs and references it by $ref everywhere it's used (properties,
items for a list, additionalProperties for a dict, anyOf for an Optional) —
those reference sites carry no format info of their own. Walking only the
top-level properties would miss a bytes field hidden inside a nested
submodel, a list of submodels, a dict of submodels, or an Optional submodel.
This module walks the full schema tree so a Feature/ApplicationService is
never published as JSON-safe when it secretly carries binary data.
"""

import dataclasses
from functools import lru_cache
from typing import Any, get_args, get_origin

from pydantic import BaseModel, TypeAdapter

from sincpro_framework.entrypoints.const import BINARY_JSON_FORMATS, BINARY_TYPES


def _is_binary_annotation(annotation: Any) -> bool:
    if annotation in BINARY_TYPES:
        return True
    origin = get_origin(annotation)
    if origin is None:
        return False
    return any(_is_binary_annotation(arg) for arg in get_args(annotation))


def _schema_has_binary(schema: dict[str, Any], seen: set[int] | None = None) -> bool:
    """Walk a Pydantic JSON Schema tree looking for binary payloads.

    1. Skip nodes already visited (cyclic $defs).
    2. Treat format binary/byte as binary.
    3. Recurse into properties, $defs, items, additionalProperties, anyOf/oneOf/allOf —
       the only places a nested submodel's own fields can be reached from.
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
                if isinstance(value, dict) and _schema_has_binary(value, seen):
                    return True
    for key in ("items", "additionalProperties"):
        nested = schema.get(key)
        if isinstance(nested, dict) and _schema_has_binary(nested, seen):
            return True
    for key in ("anyOf", "oneOf", "allOf"):
        for item in schema.get(key, []):
            if isinstance(item, dict) and _schema_has_binary(item, seen):
                return True
    return False


@lru_cache(maxsize=None)
def adapter(annotation: Any) -> TypeAdapter:
    """A cached Pydantic adapter for any shape the bus admits.

    `TypeAdapter` is what makes a dataclass — an `Entity` is one — a `list[Model]`
    or a `Model | None` validatable and schema-able without being a
    `DataTransferObject`. Building one is expensive; the catalog asks per type,
    not per call.
    """
    return TypeAdapter(annotation)


def _is_model(annotation: Any) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)


def field_annotations(dto_type: Any) -> dict[str, Any]:
    """`{field: annotation}` for a DataTransferObject or a dataclass Command."""
    if _is_model(dto_type):
        return {name: field.annotation for name, field in dto_type.model_fields.items()}
    if dataclasses.is_dataclass(dto_type):
        return {field.name: field.type for field in dataclasses.fields(dto_type)}
    return {}


def json_schema_for(annotation: Any) -> dict[str, Any]:
    """JSON Schema for a DTO, a dataclass, `list[...]`, a union — or a fallback.

    A type Pydantic cannot describe (an arbitrary object on an `Any` field, a
    forward reference that never resolved) becomes a bare object rather than an
    exception: the catalog still publishes the method, it just cannot promise
    the shape.
    """
    if annotation is None:
        return {"type": "object"}
    try:
        if _is_model(annotation):
            return annotation.model_json_schema()
        return adapter(annotation).json_schema()
    except Exception:
        title = getattr(annotation, "__name__", None)
        return {"type": "object", "title": title} if title else {"type": "object"}


def dto_json_schema(dto_type: Any) -> dict[str, Any]:
    """The input schema of one Command. Same rules as `json_schema_for`."""
    return json_schema_for(dto_type)


def is_binary_free(schema: dict[str, Any], dto_type: Any) -> bool:
    """Decide whether an already-built DTO schema can travel as JSON into a gateway.

    1. Fail if the schema tree contains binary formats.
    2. Fail if any of the DTO's own field annotations is bytes-like.
    3. Final: True only when schema and annotations are JSON-safe.
    """
    if _schema_has_binary(schema):
        return False
    return not any(
        _is_binary_annotation(annotation)
        for annotation in field_annotations(dto_type).values()
    )


def dto_is_json_serializable(dto_type: Any) -> bool:
    """Decide whether a DTO can travel as JSON into a gateway.

    Builds the schema itself — Catalog.get_scalar_use_cases() reuses an
    already-built one via is_binary_free(), to avoid computing it twice.
    """
    return is_binary_free(dto_json_schema(dto_type), dto_type)
