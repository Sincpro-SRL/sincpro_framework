"""The FastAPI wire's document stays light and is tagged: a JSON parameter points at its
definitions, written once under `components/schemas` — criteria's schema in every parameter of
every route made a 44 MB document —, and `/openapi.json` answers its `ETag`, `304` to a client
that holds it.
"""

import types
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from fastapi.testclient import TestClient

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.data_layer.repositories import MemoryRepository
from sincpro_framework.ddd import Entity, EntityReads, Query, ResponsePaginatedQuery, Search
from sincpro_framework.entrypoints import FastApiGateway
from sincpro_framework.entrypoints.entrypoint.decorators import rest


@dataclass(kw_only=True)
class Issue(Entity):
    title: str = ""


class ResponseIssues(ResponsePaginatedQuery):
    issues: list[Issue]


def _lists(count: int) -> UseFramework:
    """A tracker with `count` lists of issues, each a `GET` with its criteria."""
    bus = UseFramework("tracker", log_after_execution=False)
    bus.add_dependency("repository", MemoryRepository())
    for number in range(count):
        command = types.new_class(
            f"QueryListIssues{number}", (Search[Issue, ResponseIssues],)
        )
        handler = types.new_class(f"IssueReads{number}", (EntityReads[Issue],))
        bus.feature(command)(rest.get(f"/issues/list-{number}")(handler))
    return bus


def _references(node: Any) -> Iterator[str]:
    """Every `$ref` under `node`."""
    if isinstance(node, dict):
        if isinstance(node.get("$ref"), str):
            yield node["$ref"]
        for value in node.values():
            yield from _references(value)
    elif isinstance(node, list):
        for one in node:
            yield from _references(one)


def _document(bus: UseFramework) -> dict[str, Any]:
    return TestClient(FastApiGateway([bus], unguarded=True).app()).get("/openapi.json").json()


def test_a_criteria_parameter_points_at_one_definition_every_route_shares():
    document = _document(_lists(3))
    schemas = document["components"]["schemas"]

    for number in range(3):
        [criteria] = [
            one
            for one in document["paths"][f"/tracker/issues/list-{number}"]["get"][
                "parameters"
            ]
            if one["name"] == "criteria"
        ]
        schema = criteria["content"]["application/json"]["schema"]
        assert schema["$ref"] == "#/components/schemas/Criteria"  # its default beside it
    assert "Criteria" in schemas
    assert all(
        one.removeprefix("#/components/schemas/") in schemas
        for one in _references(document)
        if one.startswith("#/components/schemas/")
    )
    assert not any(one.startswith("#/$defs/") for one in _references(document))


def test_the_document_grows_by_a_route_not_by_a_copy_of_criteria():
    one, nineteen = _document(_lists(1)), _document(_lists(19))

    grown = len(str(nineteen)) - len(str(one))

    assert (
        grown < 18 * 4_000
    )  # each route its parameters and responses — never criteria again


def _by_quantity(bus: UseFramework) -> None:
    """A read whose filter is a `Line` of quantities."""

    class Line(DataTransferObject):
        quantity: int

    class QueryByQuantity(Query):
        line: Line

    @bus.feature(QueryByQuantity)
    @rest.get("/by-quantity")
    class ByQuantity(Feature):
        def execute(self, dto: QueryByQuantity) -> None:
            return None


def _by_price(bus: UseFramework) -> None:
    """A read whose filter is another class named `Line`, of prices."""

    class Line(DataTransferObject):
        price: float

    class QueryByPrice(Query):
        line: Line

    @bus.feature(QueryByPrice)
    @rest.get("/by-price")
    class ByPrice(Feature):
        def execute(self, dto: QueryByPrice) -> None:
            return None


def test_two_classes_of_one_name_never_point_at_each_others_shape():
    bus = UseFramework("lines", log_after_execution=False)
    _by_quantity(bus)
    _by_price(bus)

    document = _document(bus)
    shapes = {}
    for path in ("/lines/by-quantity", "/lines/by-price"):
        [line] = [
            one
            for one in document["paths"][path]["get"]["parameters"]
            if one["name"] == "line"
        ]
        schema = line["content"]["application/json"]["schema"]
        if "$ref" in schema:
            schema = document["components"]["schemas"][schema["$ref"].rsplit("/", 1)[-1]]
        shapes[path] = list(schema["properties"])

    assert shapes == {"/lines/by-quantity": ["quantity"], "/lines/by-price": ["price"]}


def test_the_document_answers_its_etag_and_304_to_a_client_that_holds_it():
    client = TestClient(FastApiGateway([_lists(2)], unguarded=True).app())

    first = client.get("/openapi.json")
    again = client.get("/openapi.json", headers={"If-None-Match": first.headers["etag"]})
    stale = client.get("/openapi.json", headers={"If-None-Match": '"another"'})

    assert first.status_code == 200 and first.headers["etag"].startswith('"')
    assert (again.status_code, again.headers["etag"], again.content) == (
        304,
        first.headers["etag"],
        b"",
    )
    assert stale.status_code == 200 and stale.json() == first.json()
