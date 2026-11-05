"""The published contract of every entity a FastAPI route answers, and the definitions its URL
parameters share, in the document FastAPI writes — each written once, under `components/schemas`.

FastAPI describes a response from its pydantic schema, which reads an entity off the dataclass and
knows nothing of what the framework says beyond it. Each route the gateway adds carries the
contracts of the entities it answers (`x-sincpro-contracts`, `contract_of`); `contracted_document`
lifts them into `components/schemas` in place of what pydantic wrote, so the FastAPI wire publishes
each model as the REST, JSON-RPC and MCP wires do: JSON Schema with `x-sincpro`.

    contracted_document({"paths": {"/sales": {"get": {"x-sincpro-contracts": {"Sale": {…}}, …}}},
                         "components": {"schemas": {"Sale": <pydantic's>}}})
        ->  the same, `components/schemas/Sale` the contract, the key gone from the operation
    referenced_document(<19 GET routes, each a `criteria` parameter>)
        ->  `components/schemas/Criteria` once, every parameter a `$ref` to it
"""

from collections.abc import Mapping
from typing import Any

CONTRACTS = "x-sincpro-contracts"
"""Where a route carries the contracts of what it answers, until the document lifts them."""

DEFINITIONS = "x-sincpro-definitions"
"""Where a route carries the definitions its URL parameters point at (`#/$defs/X`), until the
document lifts them."""


def _pointed(node: Any) -> Any:
    """`node` with every `#/$defs/X` reference pointed at `#/components/schemas/X`."""
    if isinstance(node, dict):
        return {
            key: (
                value.replace("#/$defs/", "#/components/schemas/")
                if key == "$ref" and isinstance(value, str)
                else _pointed(value)
            )
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [_pointed(one) for one in node]
    return node


def inlined(schema: Any, defs: Mapping[str, Any], seen: frozenset[str] = frozenset()) -> Any:
    """`schema` with each `#/$defs/X` replaced by X itself — what a parameter holds when no
    component may be shared for it. A model that contains itself stays an open object."""
    if isinstance(schema, dict):
        reference = schema.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/$defs/"):
            name = reference.rsplit("/", 1)[-1]
            if name in seen or name not in defs:
                return {"type": "object", "title": name}
            return inlined(defs[name], defs, seen | {name})
        return {key: inlined(value, defs, seen) for key, value in schema.items()}
    if isinstance(schema, list):
        return [inlined(one, defs, seen) for one in schema]
    return schema


def contracted_document(document: dict[str, Any]) -> dict[str, Any]:
    """The document with each entity a route answers described by its contract.

    1. Per operation, its `x-sincpro-contracts` popped.
    2. Each contract under `components/schemas` by the entity's name — the entities its
       relations point at beside it —, every reference pointed at the components.
    Final: the document; running it twice changes nothing.
    """
    schemas = document.setdefault("components", {}).setdefault("schemas", {})
    for item in document.get("paths", {}).values():
        for operation in item.values():
            if not isinstance(operation, dict):
                continue
            for name, contract in operation.pop(CONTRACTS, {}).items():
                written = dict(contract)
                for related, schema in written.pop("$defs", {}).items():
                    schemas[related] = _pointed(schema)
                schemas[name] = _pointed(written)
    return document


def referenced_document(document: dict[str, Any]) -> dict[str, Any]:
    """The document with each definition a route's URL parameters point at written once, under
    `components/schemas` — criteria's schema once, not once in every parameter of every route.

    1. Per operation, its `x-sincpro-definitions` popped.
    2. Every one the components do not hold, or hold alike, written there and its parameters
       pointed at them; when one is held with another shape — another class of the same name —
       that operation's parameters are written whole, in place, so none points at a shape not
       its own.
    Final: the document; running it twice changes nothing.
    """
    schemas = document.setdefault("components", {}).setdefault("schemas", {})
    for item in document.get("paths", {}).values():
        for operation in item.values():
            if not isinstance(operation, dict) or DEFINITIONS not in operation:
                continue
            definitions = operation.pop(DEFINITIONS)
            pointed = {name: _pointed(one) for name, one in definitions.items()}
            parameters = operation.get("parameters", [])
            if all(schemas.get(name, one) == one for name, one in pointed.items()):
                schemas.update(pointed)
                operation["parameters"] = _pointed(parameters)
            else:
                operation["parameters"] = inlined(parameters, definitions)
    return document
