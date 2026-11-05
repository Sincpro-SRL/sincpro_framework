"""What a client is published of an entity: one JSON Schema 2020-12 object, with what the framework
says beyond it under one `x-sincpro` per schema and per property — every item derived from what the
entity already declares, and readable by any JSON Schema validator.
"""

import asyncio
import importlib
import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

import pytest

from sincpro_framework import Feature, UseFramework
from sincpro_framework.data_layer.repositories import MemoryRepository
from sincpro_framework.ddd import (
    Criteria,
    Entity,
    EntityReads,
    Get,
    Presentation,
    ResponseRecord,
    ValueObject,
    When,
    check,
    compute,
)
from sincpro_framework.ddd import exceptions as ddd_exceptions
from sincpro_framework.ddd.criteria import Formulas
from sincpro_framework.ddd.criteria.services.programs import reader
from sincpro_framework.ddd.entity.entity_meta import FieldMeta, FieldType, Meta
from sincpro_framework.ddd.metamodel import StoreFacts, contract_of, description_of, model_of
from sincpro_framework.ddd.metamodel.services.contract import SAID, rendered_criteria
from sincpro_framework.ddd.metamodel.services.description import held_relations
from sincpro_framework.ddd.query import Query, ResponsePaginatedQuery
from sincpro_framework.entrypoints import FastApiGateway, McpGateway, RestGateway, RpcGateway
from sincpro_framework.entrypoints.entrypoint.decorators import mcp, rest, rpc
from sincpro_framework.introspection import entity_tags
from sincpro_framework.introspection.schemas import json_schema_for

Discount = ValueObject(Decimal, name="Discount", holds=lambda v: 0 <= v <= 30)
Quantity = ValueObject(int, name="Quantity", holds=lambda v: v > 0)


class Stage(StrEnum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"


@dataclass
class Buyer(Entity):
    name: str = ""


@dataclass
class OrderLine:
    product: str = ""
    qty: Quantity = Quantity(1)


@dataclass
class Order(Entity):
    stage: Stage = Stage.DRAFT
    buyer: Buyer | None = None
    lines: list[OrderLine] = field(default_factory=list)
    discount: Discount = Discount(Decimal("0"))
    total: Decimal = Decimal("0")
    delivery: date | None = None
    note: str = field(default="", metadata={"label": {"default": "Note", "es": "Nota"}})

    presentation = Presentation["Order"](
        required=lambda o: o.buyer,
        readonly_when=lambda o: When(o.stage != Stage.DRAFT, o.note),
    )

    @compute(lambda o: o.total, depends=lambda o: o.lines)
    def total_of(self) -> Decimal:
        return Decimal(sum(line.qty for line in self.lines))

    @check(lambda o: [o.delivery, o.stage])
    def delivery_when_confirmed(self) -> None:
        if self.stage == Stage.CONFIRMED and self.delivery is None:
            raise ddd_exceptions.InvalidValue("A confirmed order needs a delivery date")


def test_the_schema_is_json_schema_with_the_entity_said_beside_it():
    contract = contract_of(Order)

    assert contract["type"] == "object" and contract["title"] == "Order"
    assert contract["x-sincpro"]["entity"] == "Order"
    assert contract["x-sincpro"]["identity"] == "id"
    assert contract["x-sincpro"]["version"] == model_of(Order).fingerprint
    assert contract["required"] == ["buyer"]


def test_each_field_holds_what_json_schema_says_and_the_framework_says_the_rest():
    properties = contract_of(Order)["properties"]

    assert properties["stage"]["enum"] == ["draft", "confirmed"]
    assert properties["stage"]["x-sincpro"]["widget"] == "select"
    assert properties["total"] == {
        "type": "string",
        "format": "decimal",
        "x-sincpro": properties["total"]["x-sincpro"],
    }
    assert properties["total"]["x-sincpro"]["readonly"] is True
    assert properties["delivery"]["anyOf"] == [
        {"type": "string", "format": "date"},
        {"type": "null"},
    ]
    assert properties["note"]["title"] == "Note"
    assert properties["note"]["x-sincpro"]["readonly_when"] == {
        "field": "stage",
        "operator": "!=",
        "value": "draft",
    }


def test_what_a_change_reaches_is_published_derived_from_the_rules():
    properties = contract_of(Order)["properties"]

    assert properties["lines"]["x-sincpro"]["affects"] == ["total"]
    assert properties["stage"]["x-sincpro"]["judged_by"] == ["delivery_when_confirmed"]
    assert "affects" not in properties["note"]["x-sincpro"]


def test_what_a_value_object_holds_travels_as_keywords_and_whole():
    properties = contract_of(Order)["properties"]
    line = properties["lines"]["items"]["properties"]

    assert line["qty"]["exclusiveMinimum"] == 0
    assert line["qty"]["x-sincpro"]["holds"] == {
        "field": "value",
        "operator": ">",
        "value": 0,
    }
    # a decimal travels as text: its bounds only under `holds`, never as a keyword a
    # validator would skip on a string
    assert "minimum" not in properties["discount"]
    assert properties["discount"]["x-sincpro"]["holds"]["all"][0]["operator"] == ">="


def test_a_relation_points_at_the_contract_of_what_it_points_at():
    contract = contract_of(Order)

    assert contract["properties"]["buyer"]["anyOf"][0] == {"$ref": "#/$defs/Buyer"}
    assert contract["properties"]["buyer"]["x-sincpro"]["relation"] == "Buyer"
    assert contract["$defs"]["Buyer"]["x-sincpro"]["entity"] == "Buyer"


def test_how_a_record_is_read_is_published_with_only_what_it_says():
    search = contract_of(Buyer)["x-sincpro"]["search"]
    detail = Criteria.model_validate(
        {"specification": {"name": {}, "lines": {"pagination": {"limit": 20}}}}
    )

    written = rendered_criteria(detail)

    # An expression is written whole: what kind of node each part is travels with it.
    assert search == {
        "selection": {
            "field": "name",
            "operator": "like",
            "value": {"$": "param", "name": "text"},
        }
    }
    # What a blank criteria says is left out, at every depth; a scalar's is `{}`.
    assert written["specification"]["name"] == {}
    assert set(written["specification"]["lines"]) == {"pagination"}
    assert set(written) == {"specification"}
    # Whoever reads it fills the defaults back: the same criteria.
    assert Criteria.model_validate(written) == detail


def test_any_json_schema_validator_reads_it():
    jsonschema = pytest.importorskip("jsonschema")
    contract = contract_of(Order)
    validator = jsonschema.Draft202012Validator(contract)
    record = {
        "id": "0192f0c0-0000-7000-8000-000000000001",
        "stage": "draft",
        "buyer": None,
        "lines": [{"product": "desk", "qty": 2}],
        "discount": "5",
        "total": "2",
        "delivery": None,
        "note": "",
    }

    jsonschema.Draft202012Validator.check_schema(contract)
    assert not list(validator.iter_errors(record))
    refused = {
        error.json_path
        for error in validator.iter_errors(
            {**record, "stage": "void", "lines": [{"product": "desk", "qty": 0}]}
        )
    }
    assert refused == {"$.stage", "$.lines[0].qty"}


class ResponseOrders(ResponsePaginatedQuery):
    orders: list[Order]


def test_a_read_publishes_the_contract_of_the_entities_it_answers():
    schema = json_schema_for(ResponseOrders)

    assert schema["properties"]["orders"]["items"] == {"$ref": "#/$defs/Order"}
    contract = contract_of(Order)
    assert schema["$defs"]["Buyer"] == contract.pop("$defs")["Buyer"]
    assert schema["$defs"]["Order"] == contract


def test_what_a_wire_exposes_publishes_the_contract_of_what_it_answers():
    class QueryOrders(Query):
        pass

    shop = UseFramework("shop", log_after_execution=False)

    @shop.feature(QueryOrders)
    @entity_tags(Order)
    class ListOrders(Feature):
        def execute(self, dto: QueryOrders) -> ResponseOrders:
            return ResponseOrders(orders=[])

    document = RestGateway([shop]).openapi()
    schemas = document["components"]["schemas"]
    operations = [one for item in document["paths"].values() for one in item.values()]

    assert [one["x-sincpro-entities"] for one in operations] == [["Order"]] * len(operations)

    assert schemas["Order"]["x-sincpro"]["version"] == model_of(Order).fingerprint
    assert schemas["Order"]["properties"]["buyer"]["anyOf"][0] == {
        "$ref": "#/components/schemas/Buyer"
    }
    assert schemas["Buyer"]["x-sincpro"]["entity"] == "Buyer"
    assert "Meta" not in schemas and "FieldMeta" not in schemas


def test_the_fastapi_wire_publishes_the_same_contract():
    class QueryFastOrders(Query):
        pass

    shop = UseFramework("shop-fastapi", log_after_execution=False)

    @shop.feature(QueryFastOrders)
    @entity_tags(Order)
    @rest.get("/orders")
    class ListFastOrders(Feature):
        def execute(self, dto: QueryFastOrders) -> ResponseOrders:
            return ResponseOrders(orders=[])

    document = FastApiGateway([shop], unguarded=True).app().openapi()
    schemas = document["components"]["schemas"]
    operations = [one for item in document["paths"].values() for one in item.values()]

    assert schemas["Order"]["x-sincpro"]["version"] == model_of(Order).fingerprint
    assert schemas["Buyer"]["x-sincpro"]["entity"] == "Buyer"
    assert all(one["x-sincpro-entities"] == ["Order"] for one in operations)
    assert not any("x-sincpro-contracts" in one for one in operations)


# ---------------------------------------------------------------------------------------------
# Parity: the TypeScript reader (`metaFromContract` in @sincpro/criteria) reads each contract
# written here into the description Python holds — `make criteria-contract` copies the file.
# ---------------------------------------------------------------------------------------------

CONTRACT_PARITY = Path(__file__).resolve().parent / "contract-parity.json"


def _field_as_published(field: FieldMeta) -> dict[str, Any]:
    """A field of the description as a client reads it back from the contract: what
    `x-sincpro` says, the type and its kind, whether it may be empty, how it sorts and filters,
    whether a form must fill it, its choices and, inside the record, the shape it holds."""
    dumped = field.model_dump(mode="json")
    written: dict[str, Any] = {
        name: dumped[name]
        for name in SAID[1:]
        if dumped.get(name) not in (None, False, "", [], {})
    }
    written.update(
        type=dumped["type"],
        kind=dumped["kind"],
        nullable=dumped["nullable"],
        sortable=dumped["sortable"],
        ops=dumped["ops"],
    )
    if field.required:
        written["required"] = True
    if field.choices:
        written["choices"] = dumped["choices"]
    if field.type is FieldType.EMBEDDED and field.definition is not None:
        written["definition"] = _as_published(field.definition)
    return written


def _as_published(meta: Meta) -> dict[str, Any]:
    """The description as a client reads it back from the contract: a criteria with only what
    it says, every default filled by whoever reads it."""
    dumped = meta.model_dump(mode="json")
    written: dict[str, Any] = {
        "aggregate": meta.aggregate,
        "identity": meta.identity,
        "default_order": meta.default_order,
        "fields": {name: _field_as_published(one) for name, one in meta.fields.items()},
        "name": dumped["name"],
    }
    for name in ("display", "get_id"):
        if dumped.get(name):
            written[name] = dumped[name]
    for criteria, name in ((meta.search, "search"), (meta.detail, "detail")):
        if criteria is not None:
            written[name] = rendered_criteria(criteria)
    return written


FORMULA_CASES: list[dict[str, Any]] = [
    {
        "name": "a decimal times a whole number keeps the decimal's places",
        "formula": {
            "op": "*",
            "args": [{"$": "field", "name": "qty"}, {"$": "field", "name": "price"}],
        },
        "record": {"qty": 2, "price": "12.50"},
    },
    {
        "name": "whole numbers stay whole",
        "formula": {
            "op": "*",
            "args": [{"$": "field", "name": "qty"}, {"$": "field", "name": "qty"}],
        },
        "record": {"qty": 3},
    },
    {
        "name": "decimals multiplied add their places; a sum keeps the most",
        "formula": {
            "op": "+",
            "args": [
                {
                    "op": "-",
                    "args": [
                        {"$": "field", "name": "subtotal"},
                        {"op": "*", "args": [{"$": "field", "name": "discount"}, "1.13"]},
                    ],
                },
                {"op": "-", "args": [{"$": "field", "name": "discount"}]},
            ],
        },
        "record": {"subtotal": "25.30", "discount": "1.5"},
    },
    {
        "name": "a sum over the records a field holds, from a decimal zero",
        "formula": {
            "op": "sum",
            "of": "lines",
            "each": {"$": "field", "name": "amount"},
            "from": "0",
        },
        "record": {"lines": [{"amount": "25.00"}, {"amount": "0.30"}]},
    },
    {
        "name": "a sum over no record is its start",
        "formula": {
            "op": "sum",
            "of": "lines",
            "each": {"$": "field", "name": "amount"},
            "from": "0",
        },
        "record": {"lines": []},
    },
    {
        "name": "a sum of whole numbers from a whole zero",
        "formula": {
            "op": "sum",
            "of": "lines",
            "each": {"$": "field", "name": "qty"},
            "from": 0,
        },
        "record": {"lines": [{"qty": 2}, {"qty": 5}]},
    },
]
"""Formulas over records as JSON carries them — a decimal as text —, answered by Python."""


def _answered(case: dict[str, Any]) -> Any:
    """What Python answers, as JSON writes it: a decimal as its text, places included."""
    value = Formulas.value_of(case["formula"], reader(case["record"]))
    return str(value) if isinstance(value, Decimal) else value


def contract_parity() -> dict[str, Any]:
    """What both repositories read: each contract, and the description it must read into."""
    return {
        "about": (
            "Contracts the framework publishes and the description each one reads into. Written "
            "by tests/ddd/metamodel/test_contract.py and read by @sincpro/criteria's "
            "metaFromContract. Do not edit by hand."
        ),
        "cases": [
            {
                "name": entity.__name__,
                "contract": contract_of(entity),
                "expected": _as_published(
                    description_of(
                        entity,
                        StoreFacts(
                            identity="id",
                            relations=held_relations(model_of(entity), False),
                        ),
                    )
                ),
            }
            for entity in (Order, Buyer)
        ],
        "formulas": [{**one, "expected": _answered(one)} for one in FORMULA_CASES],
    }


def test_the_contract_parity_file_is_what_the_framework_writes():
    written = json.loads(CONTRACT_PARITY.read_text()) if CONTRACT_PARITY.exists() else None

    assert written == json.loads(
        json.dumps(contract_parity())
    ), "run `poetry run python -m tests.ddd.metamodel.test_contract` to write it"


if __name__ == "__main__":
    # Written from the module under its own name: a fingerprint names the classes it reads by
    # their module, and the suite imports this one as `tests.ddd.metamodel.test_contract`.
    published = importlib.import_module("tests.ddd.metamodel.test_contract")
    published.CONTRACT_PARITY.write_text(
        json.dumps(published.contract_parity(), indent=2, ensure_ascii=False) + "\n"
    )
    print(f"wrote {published.CONTRACT_PARITY}")


def test_an_agent_reads_the_models_of_what_the_mcp_wire_exposes():
    fastmcp = pytest.importorskip("fastmcp")  # the [mcp] extra

    class QueryMcpOrders(Query):
        pass

    shop = UseFramework("shop-mcp", log_after_execution=False)

    @shop.feature(QueryMcpOrders)
    @entity_tags(Order)
    @mcp()
    class ListMcpOrders(Feature):
        def execute(self, dto: QueryMcpOrders) -> ResponseOrders:
            return ResponseOrders(orders=[])

    server = McpGateway([shop], unguarded=True).server()

    async def read() -> tuple[list[str], Any]:
        async with fastmcp.Client(server) as client:
            listed = [str(one.uri) for one in await client.list_resources()]
            contents = await client.read_resource(listed[0])
            [written] = contents
            return listed, json.loads(written.text)

    listed, metamodel = asyncio.run(read())

    assert listed == ["metamodel://shop-mcp"]
    assert metamodel["models"]["Order"]["x-sincpro"]["version"] == model_of(Order).fingerprint
    listed_orders = metamodel["operations"]["shop-mcp.QueryMcpOrders"]
    assert listed_orders["entities"] == ["Order"]
    # What it is and what it answers ride along: the resource IS `metamodel_of`.
    assert listed_orders["role"] == "command"
    assert listed_orders["query"] is True
    assert listed_orders["output"]["properties"]["orders"]["items"] == {
        "$ref": "#/models/Order"
    }


class ResponseOrderRead(ResponseRecord):
    order: Order


class QueryGetOrder(Get[Order, ResponseOrderRead], Query):
    pass


def a_bus_whose_read_declares_no_answer() -> UseFramework:
    """The usual read: `EntityReads` answers `Get[Order, …]` and its `execute` declares nothing,
    so the answer's schema says nothing of `Order` — the operation still belongs to it."""
    shop = UseFramework("shop-reads", log_after_execution=False)
    shop.add_dependency("repository", MemoryRepository())

    @shop.feature(QueryGetOrder)
    @rest.get("/orders/{id}")
    @rpc()
    class OrderReads(EntityReads[Order]):
        pass

    return shop


def test_every_document_publishes_the_entity_a_read_belongs_to():
    expected = contract_of(Order)
    expected.pop("$defs")

    fastapi = FastApiGateway([a_bus_whose_read_declares_no_answer()], unguarded=True)
    rest_wire = RestGateway([a_bus_whose_read_declares_no_answer()])
    rpc_wire = RpcGateway([a_bus_whose_read_declares_no_answer()], unguarded=True)

    for document in (
        fastapi.app().openapi(),
        rest_wire.openapi(),
        rpc_wire.discover(),
    ):
        schemas = document["components"]["schemas"]
        assert schemas["Order"]["x-sincpro"] == expected["x-sincpro"]
        assert schemas["Buyer"]["x-sincpro"]["entity"] == "Buyer"
