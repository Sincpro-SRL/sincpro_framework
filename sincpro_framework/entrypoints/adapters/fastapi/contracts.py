"""The published contract of every entity a FastAPI route answers, in the document FastAPI writes.

FastAPI describes a response from its pydantic schema, which reads an entity off the dataclass and
knows nothing of what the framework says beyond it. Each route the gateway adds carries the
contracts of the entities it answers (`x-sincpro-contracts`, `contract_of`); `contracted_document`
lifts them into `components/schemas` in place of what pydantic wrote, so the FastAPI wire publishes
each model as the REST, JSON-RPC and MCP wires do: JSON Schema with `x-sincpro`.

    contracted_document({"paths": {"/sales": {"get": {"x-sincpro-contracts": {"Sale": {…}}, …}}},
                         "components": {"schemas": {"Sale": <pydantic's>}}})
        ->  the same, `components/schemas/Sale` the contract, the key gone from the operation
"""

from typing import Any

CONTRACTS = "x-sincpro-contracts"
"""Where a route carries the contracts of what it answers, until the document lifts them."""


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
