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

from typing import Any, get_args, get_origin

from sincpro_framework.entrypoints.const import BINARY_JSON_FORMATS, BINARY_TYPES
from sincpro_framework.sincpro_abstractions import DataTransferObject


def dto_json_schema(dto_type: type[DataTransferObject]) -> dict[str, Any]:
    try:
        return dto_type.model_json_schema()
    except Exception:
        return {"type": "object", "title": dto_type.__name__}


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


def is_binary_free(schema: dict[str, Any], dto_type: type[DataTransferObject]) -> bool:
    """Decide whether an already-built DTO schema can travel as JSON into a gateway.

    1. Fail if the schema tree contains binary formats.
    2. Fail if any of the DTO's own field annotations is bytes-like.
    3. Final: True only when schema and annotations are JSON-safe.
    """
    if _schema_has_binary(schema):
        return False
    return not any(
        _is_binary_annotation(field.annotation) for field in dto_type.model_fields.values()
    )


def dto_is_json_serializable(dto_type: type[DataTransferObject]) -> bool:
    """Decide whether a DTO can travel as JSON into a gateway.

    Builds the schema itself — Catalog.build_scalar_feature_and_app_services()
    reuses an already-built one via is_binary_free(), to avoid computing it twice.
    """
    try:
        schema = dto_type.model_json_schema()
    except Exception:
        return False
    return is_binary_free(schema, dto_type)
