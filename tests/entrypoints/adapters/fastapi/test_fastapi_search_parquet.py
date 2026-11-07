"""A search's page as JSON or as Parquet: chosen by the caller, or — `auto` — Parquet when more
records are asked for than the search's `parquet_above`, over a wire whose caller reads it.
Inside the process an answer is always objects."""

import io
from dataclasses import dataclass
from decimal import Decimal

import pyarrow.parquet
from fastapi.testclient import TestClient

from sincpro_framework import UseFramework
from sincpro_framework.data_layer.data_analysis.export import collection_from_parquet
from sincpro_framework.data_layer.repositories import MemoryRepository
from sincpro_framework.ddd import Entity, EntityReads, ResponsePaginatedQuery, Search
from sincpro_framework.ddd.criteria import Criteria, Pagination
from sincpro_framework.ddd.entity.entity_collection import Count
from sincpro_framework.entrypoints import FastApiGateway
from sincpro_framework.entrypoints.entrypoint.decorators import rest

PARQUET = "application/vnd.apache.parquet"


@dataclass(kw_only=True)
class Entry(Entity):
    number: int = 0
    amount: Decimal = Decimal("0")


class ResponseEntries(ResponsePaginatedQuery):
    entries: list[Entry]


class QueryListEntries(Search[Entry, ResponseEntries]):
    parquet_above = 5


class QueryListEntriesAsJson(Search[Entry, ResponseEntries]):
    parquet_above = None


@rest.get("/entries")
class EntryReads(EntityReads[Entry]):
    pass


@rest.get("/entries/json")
class EntryJsonReads(EntityReads[Entry]):
    pass


def _bus(count: int = 7) -> UseFramework:
    repository = MemoryRepository()
    repository.save([Entry(number=n, amount=Decimal(f"{n}.25")) for n in range(1, count + 1)])
    bus = UseFramework("ledger", log_after_execution=False)
    bus.add_dependency("repository", repository)
    bus.feature(QueryListEntries)(EntryReads)
    bus.feature(QueryListEntriesAsJson)(EntryJsonReads)
    return bus


def _criteria(limit: int) -> str:
    return Criteria(pagination=Pagination(limit=limit)).model_dump_json(exclude_defaults=True)


def test_a_large_page_asked_by_a_caller_that_reads_parquet_answers_parquet():
    client = TestClient(FastApiGateway([_bus()], unguarded=True).app())

    answer = client.get(
        "/ledger/entries",
        params={"criteria": _criteria(10)},
        headers={"Accept": f"application/json, {PARQUET}"},
    )

    assert answer.headers["content-type"] == PARQUET
    page = collection_from_parquet(answer.content, Entry)
    assert sorted(one.number for one in page) == [1, 2, 3, 4, 5, 6, 7]
    assert page.cursor is None and page.count == Count(value=7, exact=True)
    assert isinstance(page[0].amount, Decimal)
    footer = pyarrow.parquet.read_metadata(io.BytesIO(answer.content)).metadata
    assert b'"records": "entries"' in footer[b"sincpro"]


def test_a_small_page_or_a_caller_that_reads_only_json_gets_json():
    client = TestClient(FastApiGateway([_bus()], unguarded=True).app())

    small = client.get(
        "/ledger/entries", params={"criteria": _criteria(3)}, headers={"Accept": PARQUET}
    )
    json_only = client.get("/ledger/entries", params={"criteria": _criteria(10)})
    never = client.get(
        "/ledger/entries/json",
        params={"criteria": _criteria(10)},
        headers={"Accept": PARQUET},
    )

    assert small.headers["content-type"].startswith("application/json")
    assert len(small.json()["entries"]) == 3 and small.json()["cursor"] is not None
    assert json_only.headers["content-type"].startswith("application/json")
    assert never.headers["content-type"].startswith("application/json")


def test_parquet_asked_for_is_parquet_whatever_its_size_with_the_cursor_to_go_on():
    client = TestClient(FastApiGateway([_bus()], unguarded=True).app())

    answer = client.get(
        "/ledger/entries", params={"criteria": _criteria(2), "format": "parquet"}
    )

    page = collection_from_parquet(answer.content, Entry)
    assert answer.headers["content-type"] == PARQUET
    assert len(page) == 2 and page.cursor is not None
    assert page.count == Count(value=7, exact=True)


def test_inside_the_process_an_answer_is_objects_however_large():
    bus = _bus()

    answer = bus(
        QueryListEntries(criteria=Criteria(pagination=Pagination(limit=10))), ResponseEntries
    )

    assert isinstance(answer, ResponseEntries) and len(answer.entries) == 7


def test_the_document_and_the_contract_say_a_search_may_answer_parquet():
    from sincpro_framework.entrypoints.services.client_contract import client_contracts

    gateway = FastApiGateway([_bus()], unguarded=True)
    document = TestClient(gateway.app()).get("/openapi.json").json()
    contract = client_contracts([gateway])["ledger"]["operations"]

    answers = document["paths"]["/ledger/entries"]["get"]["responses"]["200"]["content"]
    assert {"application/json", PARQUET} <= set(answers)
    assert contract["ledger.QueryListEntries"]["formats"] == ["json", "parquet"]
    assert contract["ledger.QueryListEntries"]["parquet_above"] == 5
    assert contract["ledger.QueryListEntriesAsJson"]["formats"] == ["json"]


def test_a_caller_that_refuses_parquet_with_q_0_gets_json():
    client = TestClient(FastApiGateway([_bus()], unguarded=True).app())

    answer = client.get(
        "/ledger/entries",
        params={"criteria": _criteria(10)},
        headers={"Accept": f"application/json, {PARQUET};q=0"},
    )

    assert answer.headers["content-type"].startswith("application/json")


def test_what_the_caller_reads_is_never_handed_to_a_use_case_called_inside():
    from sincpro_framework.context import use_context
    from sincpro_framework.ddd.query import ACCEPTED_FORMATS
    from sincpro_framework.ddd.reads.reads import encoding_of

    asked = Criteria(pagination=Pagination(limit=10))
    with use_context().scoped({ACCEPTED_FORMATS: {"for": "QueryOther", "types": [PARQUET]}}):
        inside = encoding_of(QueryListEntries(criteria=asked), asked)
    with use_context().scoped(
        {ACCEPTED_FORMATS: {"for": "QueryListEntries", "types": [PARQUET]}}
    ):
        asked_by_the_wire = encoding_of(QueryListEntries(criteria=asked), asked)

    assert inside == "json" and asked_by_the_wire == "parquet"
