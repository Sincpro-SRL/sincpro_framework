"""An export over HTTP: `Export[Entity, Streamed]` answered by the entity's reads, streamed as
it is written — never a JSON body, never held whole."""

import io
import json
from dataclasses import dataclass
from decimal import Decimal

import pyarrow.parquet
from fastapi.testclient import TestClient

from sincpro_framework import UseFramework
from sincpro_framework.data_layer.repositories import MemoryRepository
from sincpro_framework.ddd import Entity, EntityReads, Export, Streamed
from sincpro_framework.entrypoints import FastApiGateway
from sincpro_framework.entrypoints.entrypoint.decorators import rest


@dataclass(kw_only=True)
class Sale(Entity):
    number: int = 0
    total: Decimal = Decimal("0")


class QueryExportSales(Export[Sale, Streamed]):
    pass


@rest.get("/sales/export")
class SaleReads(EntityReads[Sale]):
    pass


def _client(count: int) -> TestClient:
    repository = MemoryRepository()
    repository.save([Sale(number=n, total=Decimal(f"{n}.50")) for n in range(1, count + 1)])
    bus = UseFramework("shop", log_after_execution=False)
    bus.add_dependency("repository", repository)
    bus.feature(QueryExportSales)(SaleReads)
    return TestClient(FastApiGateway([bus], unguarded=True).app())


def test_an_export_streams_json_lines_with_the_name_to_save_it_under():
    answer = _client(7).get("/shop/sales/export", params={"format": "jsonl"})

    lines = [json.loads(one) for one in answer.text.splitlines()]
    assert answer.status_code == 200
    assert answer.headers["content-type"].startswith("application/jsonl")
    assert answer.headers["content-disposition"] == 'attachment; filename="sale.jsonl"'
    assert sorted(one["number"] for one in lines) == list(range(1, 8))
    assert {one["total"] for one in lines} >= {"1.50", "7.50"}


def test_an_export_streams_a_parquet_file_by_default():
    answer = _client(12).get("/shop/sales/export")

    table = pyarrow.parquet.read_table(io.BytesIO(answer.content))
    assert answer.headers["content-type"] == "application/vnd.apache.parquet"
    assert table.num_rows == 12
    assert sorted(table.column("number").to_pylist()) == list(range(1, 13))


def test_the_document_says_an_export_answers_bytes_never_a_json_body():
    document = _client(1).get("/openapi.json").json()

    answers = document["paths"]["/shop/sales/export"]["get"]["responses"]["200"]["content"]
    assert set(answers) == {
        "application/vnd.apache.parquet",
        "application/vnd.apache.arrow.stream",
        "application/jsonl",
    }
    assert all("schema" not in one for one in answers.values())


def test_the_client_contract_says_the_answer_is_streamed_bytes():
    from sincpro_framework.entrypoints.services.client_contract import client_contracts

    repository = MemoryRepository()
    bus = UseFramework("shop", log_after_execution=False)
    bus.add_dependency("repository", repository)
    bus.feature(QueryExportSales)(SaleReads)

    contract = client_contracts([FastApiGateway([bus], unguarded=True)])["shop"]
    operation = contract["operations"]["shop.QueryExportSales"]
    output = operation["output"]

    assert operation["role"] == "export" and operation["query"] is True

    assert output["x-sincpro-streamed"] == [
        "application/vnd.apache.parquet",
        "application/vnd.apache.arrow.stream",
        "application/jsonl",
    ]
    assert "chunks" not in output["properties"]
