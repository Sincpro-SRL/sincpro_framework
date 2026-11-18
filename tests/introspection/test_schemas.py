"""A JSON Schema is generated once per type, and every caller gets one it may write on.

Pydantic builds a schema afresh on every call, and a service asks for the same Command's from each
gateway's catalog, each route and each document while it starts — the incident was a service of
fifty routes generating its schemas some three hundred times before it answered a request.
"""

import pytest
from pydantic import BaseModel

from sincpro_framework import DataTransferObject
from sincpro_framework.introspection.schemas import json_schema_for, schema_of


class CommandIssueInvoice(DataTransferObject):
    customer: str
    total: int


def test_a_schema_is_generated_once_per_type(monkeypatch: pytest.MonkeyPatch) -> None:
    class CommandCountedOnce(DataTransferObject):
        total: int

    generated: list[str] = []
    original = CommandCountedOnce.model_json_schema

    def counted(*args, **kwargs):
        generated.append("once")
        return original(*args, **kwargs)

    monkeypatch.setattr(CommandCountedOnce, "model_json_schema", counted)
    for _ in range(3):
        schema_of(CommandCountedOnce)
        json_schema_for(CommandCountedOnce)
    assert generated == ["once"]


def test_a_schema_handed_out_can_be_written_on_without_changing_the_next() -> None:
    first = json_schema_for(CommandIssueInvoice)
    first["properties"].clear()
    first["required"].append("nothing")
    assert set(json_schema_for(CommandIssueInvoice)["properties"]) == {"customer", "total"}
    assert schema_of(CommandIssueInvoice)["required"] == ["customer", "total"]


def test_each_reference_template_is_its_own_schema() -> None:
    class Line(BaseModel):
        sku: str

    class CommandWithLines(DataTransferObject):
        lines: list[Line]

    plain = schema_of(CommandWithLines)
    for_openapi = schema_of(CommandWithLines, "#/components/schemas/{model}")
    assert plain["properties"]["lines"]["items"] == {"$ref": "#/$defs/Line"}
    assert for_openapi["properties"]["lines"]["items"] == {
        "$ref": "#/components/schemas/Line"
    }


def test_a_model_whose_reference_resolves_later_is_described_with_it() -> None:
    class CommandShip(DataTransferObject):
        to: "Address"  # noqa: F821 — declared below, on purpose

    assert json_schema_for(CommandShip) == {"type": "object", "title": "CommandShip"}

    class Address(BaseModel):
        street: str

    CommandShip.model_rebuild(_types_namespace={"Address": Address})
    assert "street" in json_schema_for(CommandShip)["$defs"]["Address"]["properties"]
