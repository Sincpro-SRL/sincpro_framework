"""How any annotation a bus admits is described as JSON Schema — a Command, what it answers, a
`list[...]`, a union —, each entity it reaches as its published contract (`contract_of`): the one
description every discovery document and the metamodel start from.

    json_schema_for(list[Invoice])     ->  {"type": "array", "items": {"$ref": "#/$defs/Invoice"},
                                            "$defs": {"Invoice": <contract_of(Invoice)>, …}}
    adapter(ResponseInvoice)           ->  the one TypeAdapter of it, built once

Context: describing a type is no wire's: REST, JSON-RPC, MCP and gRPC publish it, the metamodel
exports it. What only a wire decides — whether a payload may carry bytes — stays with the wires.
"""

from functools import lru_cache
from typing import Any

from pydantic import BaseModel, TypeAdapter

from sincpro_framework.ddd.metamodel import contract_of
from sincpro_framework.ddd.metamodel.services.contract import entities_in


@lru_cache(maxsize=None)
def adapter(annotation: Any) -> TypeAdapter:
    """A cached Pydantic adapter for any shape the bus admits.

    `TypeAdapter` is what makes a dataclass — an `Entity` is one — a `list[Model]`
    or a `Model | None` validatable and schema-able without being a
    `DataTransferObject`. Building one is expensive; the catalog asks per type,
    not per call.
    """
    return TypeAdapter(annotation)


def contracted(schema: dict[str, Any], annotation: Any) -> dict[str, Any]:
    """`schema` with every entity `annotation` reaches described by its published contract —
    JSON Schema with `x-sincpro` (`contract_of`) — in place of what pydantic read off the class.

        contracted(<ResponseSales' schema>, ResponseSales)
            ->  the same schema, `$defs.Sale` the contract of Sale, `$defs.Customer` the one of
                the entity Sale points at

    1. The entities reached (`entities_in`); none, the schema as it is.
    2. Each one's contract under `$defs` by its name, the entities its relations point at beside
       it; the schema itself when it describes the entity.
    Final: one description, whichever wire publishes it.
    """
    entities = entities_in(annotation, set())
    if not entities:
        return schema
    written = dict(schema)
    defs = dict(written.get("$defs") or {})
    for entity in entities:
        contract = contract_of(entity)
        defs.update(contract.pop("$defs", {}))
        if entity is annotation:
            written = {**contract, "$defs": defs}
        else:
            defs[entity.__name__] = contract
    if defs:
        written["$defs"] = defs
    return written


def json_schema_for(annotation: Any) -> dict[str, Any]:
    """JSON Schema for a DTO, a dataclass, `list[...]`, a union — or a fallback.

    A type Pydantic cannot describe (an arbitrary object on an `Any` field, a
    forward reference that never resolved) becomes a bare object rather than an
    exception: the catalog still publishes the method, it just cannot promise
    the shape.

        json_schema_for(list[int])       ->  {"items": {"type": "integer"}, "type": "array"}
        json_schema_for(None)            ->  {"type": "object"}
        json_schema_for(<a type Pydantic cannot describe, named Socket>)
        ->  {"type": "object", "title": "Socket"}

    A pydantic model answers its own `model_json_schema`; anything else goes through a
    cached `TypeAdapter`. An entity it reaches is described by its published contract
    (`contracted`), not by what pydantic reads off the dataclass.
    """
    if annotation is None:
        return {"type": "object"}
    try:
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            return contracted(annotation.model_json_schema(), annotation)
        return contracted(adapter(annotation).json_schema(), annotation)
    except Exception:
        title = getattr(annotation, "__name__", None)
        return {"type": "object", "title": title} if title else {"type": "object"}
