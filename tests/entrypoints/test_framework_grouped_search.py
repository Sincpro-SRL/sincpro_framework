"""A list grouped over the wire: the same `Search`, the same route, the criteria's `grouping`
deciding what comes back — what `@sincpro/criteria`'s `restSource` reads, with no route of its
own for groups. The answer's first array that is not the envelope holds the groups, so a client
reading «the one array beside cursor and count» reads them.
"""

from dataclasses import dataclass

import pytest

from sincpro_framework import UseFramework
from sincpro_framework.data_layer.repositories import MemoryRepository
from sincpro_framework.ddd import Entity, EntityReads, ResponsePaginatedQuery, Search
from sincpro_framework.entrypoints.entrypoint.decorators import rest

ENVELOPE = {"cursor", "count", "model_version", "dropped", "missing"}
"""What `@sincpro/criteria` skips to find what an answer holds (`recordsOf`)."""


@dataclass(kw_only=True)
class Fee(Entity):
    state: str = ""
    amount: int = 0


class ResponseFees(ResponsePaginatedQuery):
    fees: list[Fee]


class QueryListFees(Search[Fee, ResponseFees]):
    pass


def _client():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from sincpro_framework.entrypoints import FastApiGateway

    bus = UseFramework("dues", log_after_execution=False)
    repository = MemoryRepository()
    repository.save(
        [Fee(state="paid", amount=n) for n in range(3)] + [Fee(state="due", amount=9)]
    )
    bus.add_dependency("repository", repository)

    @bus.feature(QueryListFees)
    @rest.get("/fees")
    class FeeReads(EntityReads[Fee]):
        pass

    return TestClient(FastApiGateway([bus]).app())


def _held(answer: dict) -> list:
    """The one array beside the envelope, read the way the client reads it."""
    return next(
        value
        for name, value in answer.items()
        if name not in ENVELOPE and isinstance(value, list)
    )


def test_a_grouped_list_answers_its_groups_where_the_client_reads_the_records():
    criteria = (
        '{"grouping": {"group_by": [{"field": "state"}],'
        ' "measures": {"total": {"function": "sum", "field": "amount"}}},'
        ' "pagination": {"limit": 1}}'
    )

    answered = _client().get("/dues/fees", params={"criteria": criteria})

    assert answered.status_code == 200, answered.text
    groups = {one["value"]: one for one in _held(answered.json())}
    assert {value: one["count"] for value, one in groups.items()} == {"paid": 3, "due": 1}
    assert groups["paid"]["measures"] == {"total": 3}
    assert len(groups["paid"]["ids"]) == 1 and groups["paid"]["cursor"]
    assert groups["paid"]["criteria"]["selection"]["value"] == "paid"


def test_a_list_that_groups_nothing_answers_its_records_as_always():
    answered = _client().get("/dues/fees")

    assert answered.status_code == 200, answered.text
    assert "groups" not in answered.json()
    assert len(_held(answered.json())) == 4
