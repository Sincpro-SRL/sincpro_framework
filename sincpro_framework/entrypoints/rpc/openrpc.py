"""Build an OpenRPC 1.4 document from the JSON-RPC catalog."""

from typing import Any

from sincpro_framework.entrypoints.catalog import Operation, layer_for_kind
from sincpro_framework.entrypoints.rpc.protocol import DISCOVER_METHOD

OPENRPC_VERSION = "1.4.0"


def content_descriptors(schema: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn a DTO JSON Schema object into OpenRPC by-name params.

    1. Read properties and required from the DTO schema.
    2. Each field becomes a Content Descriptor (name, required, schema).
    3. Final: descriptors in the same order Pydantic emitted the properties.
    """
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    descriptors: list[dict[str, Any]] = []
    for name, field_schema in properties.items():
        if not isinstance(field_schema, dict):
            continue
        descriptors.append(
            {
                "name": name,
                "required": name in required,
                "schema": field_schema,
            }
        )
    return descriptors


def method_object(name: str, operation: Operation, instance: str) -> dict[str, Any]:
    layer = layer_for_kind(operation.kind)
    return {
        "name": name,
        "summary": operation.description.split("\n", 1)[0],
        "description": operation.description,
        "tags": [{"name": instance}, {"name": layer}, {"name": operation.kind}],
        "paramStructure": "by-name",
        "params": content_descriptors(operation.json_schema),
        "result": {
            "name": "result",
            "schema": {"type": "object"},
        },
        "x-sincpro-instance": instance,
        "x-sincpro-layer": layer,
        "x-sincpro-dto": operation.name,
    }


def discover_method_object() -> dict[str, Any]:
    return {
        "name": DISCOVER_METHOD,
        "summary": "OpenRPC service discovery",
        "description": "Return this server's OpenRPC document.",
        "params": [],
        "result": {
            "name": "OpenRPC Document",
            "schema": {"type": "object"},
        },
    }


def openrpc_document(
    title: str,
    methods: list[dict[str, Any]],
    version: str = "1.0.0",
) -> dict[str, Any]:
    return {
        "openrpc": OPENRPC_VERSION,
        "info": {"title": title, "version": version},
        "methods": [discover_method_object(), *methods],
    }
