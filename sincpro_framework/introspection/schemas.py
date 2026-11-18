"""How any annotation a bus admits is described as JSON Schema — a Command, what it answers, a
`list[...]`, a union —, each entity it reaches as its published contract (`contract_of`): the one
description every discovery document and the metamodel start from.

    json_schema_for(list[Invoice])     ->  {"type": "array", "items": {"$ref": "#/$defs/Invoice"},
                                            "$defs": {"Invoice": <contract_of(Invoice)>, …}}
    adapter(ResponseInvoice)           ->  the one TypeAdapter of it, built once

Context: describing a type is no wire's: REST, JSON-RPC, MCP and gRPC publish it, the metamodel
exports it. What only a wire decides — whether a payload may carry bytes — stays with the wires.
"""

import copy
from functools import lru_cache
from typing import Any

from pydantic import BaseModel, TypeAdapter
from pydantic.json_schema import DEFAULT_REF_TEMPLATE

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


_generated: dict[tuple[Any, str], tuple[Any, dict[str, Any]]] = {}
"""Each schema `schema_of` generated, by annotation and reference template, with the core schema
it was generated from."""


def schema_of(annotation: Any, ref_template: str = DEFAULT_REF_TEMPLATE) -> dict[str, Any]:
    """The JSON Schema pydantic generates for `annotation` — generated once, a copy answered.

        schema_of(CommandIssueInvoice)        ->  CommandIssueInvoice.model_json_schema()
        schema_of(list[Invoice])              ->  adapter(list[Invoice]).json_schema()
        schema_of(CommandIssueInvoice, "#/components/schemas/{model}")   its refs written so

    Context: pydantic builds a JSON Schema afresh on every call, and one service asks for the same
    Command's many times while it starts — each gateway's catalog, each wire's routes and
    documents. A service of fifty-five routes generated some 380 schemas for 75 classes; once per
    class, building its REST app took 60% less time.

    1. What the annotation is checked by: a model's own core schema, or its cached adapter's.
    2. Generated before from that same core schema: the held one. A model rebuilt since
       (`model_rebuild`, a forward reference resolved) has another, and is generated again.
    3. Generated — a model by its `model_json_schema`, anything else by its adapter — and held
       only once it succeeded: a model whose forward references are still open raises, and is
       asked again next time.
    Final: a deep copy — the caller may write on it.
    """
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        source: Any = annotation.__pydantic_core_schema__
        generate = annotation.model_json_schema
    else:
        checker = adapter(annotation)
        source, generate = checker.core_schema, checker.json_schema
    held = _generated.get((annotation, ref_template))
    if held is None or held[0] is not source:
        held = _generated[(annotation, ref_template)] = (
            source,
            generate(ref_template=ref_template),
        )
    return copy.deepcopy(held[1])


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

    What pydantic generates (`schema_of`, once per type), with every entity it reaches
    described by its published contract (`contracted`), not by what pydantic reads off the
    dataclass.
    """
    if annotation is None:
        return {"type": "object"}
    try:
        return contracted(schema_of(annotation), annotation)
    except Exception:
        title = getattr(annotation, "__name__", None)
        return {"type": "object", "title": title} if title else {"type": "object"}
