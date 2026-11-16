"""The framework's `Delete` — and `Archive`, its trash — served as what they are: writes that
answer nothing. Every wire says so without the project declaring it — a `204` over REST, `null`
in the contract, the role beside the entity — and a dry run still answers the `Preview` a
confirmation shows. A field sent where the route does not read it is refused, never dropped: a
`dry_run` dropped would run the real write.
"""

from dataclasses import dataclass

import pytest

from sincpro_framework import UseFramework
from sincpro_framework.data_layer.repositories import MemoryRepository
from sincpro_framework.ddd import ArchivableMixin, Archive, Delete, Entity, EntityEdits
from sincpro_framework.entrypoints.entrypoint.decorators import rest
from sincpro_framework.introspection import metamodel_of


@dataclass(kw_only=True)
class Issue(ArchivableMixin, Entity):
    title: str = ""


class CommandDeleteIssue(Delete[Issue]):
    pass


class CommandTrashIssue(Archive[Issue]):
    pass


def tracker() -> tuple[UseFramework, MemoryRepository, Issue]:
    bus = UseFramework("tracker", log_after_execution=False)
    repository = MemoryRepository()
    bus.add_dependency("repository", repository)
    stored = Issue(title="Printer jammed")
    repository.save(stored)

    @bus.feature(CommandDeleteIssue)
    @rest.delete("/issues/{id}")
    class IssueForms(EntityEdits[Issue]):
        pass

    @bus.feature(CommandTrashIssue)
    @rest.post("/issues/{id}/archive")
    class IssueTrash(EntityEdits[Issue]):
        pass

    return bus, repository, stored


def _client(bus: UseFramework):
    """FastAPI's test client over the gateway, or the test skipped where the extra is not
    installed."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from sincpro_framework.entrypoints import FastApiGateway

    return TestClient(FastApiGateway([bus], unguarded=True).app())


def test_a_delete_route_answers_204_with_no_body_and_the_record_is_gone():
    bus, repository, stored = tracker()

    answered = _client(bus).delete(f"/tracker/issues/{stored.id}")

    assert answered.status_code == 204, answered.text
    assert answered.content == b""
    assert repository.get(Issue, stored.id) is None


def test_its_dry_run_answers_the_preview_and_deletes_nothing():
    bus, repository, stored = tracker()

    answered = _client(bus).delete(f"/tracker/issues/{stored.id}?dry_run=true")

    assert answered.status_code == 200, answered.text
    assert answered.json() == {
        "text": f"Would delete Issue {stored.id}",
        "data": {"id": stored.id, "version": 1},
    }
    assert repository.get(Issue, stored.id) is not None


def test_the_document_and_the_contract_say_it_deletes_and_answers_nothing():
    bus, _, _ = tracker()

    route = (
        _client(bus).get("/openapi.json").json()["paths"]["/tracker/issues/{id}"]["delete"]
    )
    operation = metamodel_of(bus)["operations"]["tracker.CommandDeleteIssue"]

    assert route["x-sincpro-role"] == "delete" and "x-sincpro-query" not in route
    assert set(route["responses"]) >= {"204", "200"}
    assert route["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/Preview"
    }
    assert (operation["role"], operation["query"], operation["output"]) == (
        "delete",
        False,
        {"type": "null"},
    )
    assert operation["entities"] == ["Issue"]


def test_a_dry_run_sent_in_a_delete_body_is_refused_and_nothing_is_deleted():
    bus, repository, stored = tracker()

    answered = _client(bus).request(
        "DELETE", f"/tracker/issues/{stored.id}", json={"dry_run": True, "origin_version": 1}
    )

    assert answered.status_code == 422, answered.text
    errors = answered.json()["errors"]
    assert [one["pointer"] for one in errors] == ["#/dry_run", "#/origin_version"]
    assert errors[0]["detail"] == "DELETE reads dry_run from the query string: send it there"
    assert repository.get(Issue, stored.id) is not None


def test_an_archive_route_answers_204_and_reads_its_dry_run_from_the_body_only():
    bus, repository, stored = tracker()
    client = _client(bus)
    url = f"/tracker/issues/{stored.id}/archive"

    in_the_query = client.post(f"{url}?dry_run=true", json={})
    rehearsed = client.post(url, json={"dry_run": True})
    archived = client.post(url, json={})

    assert in_the_query.status_code == 422, in_the_query.text
    assert rehearsed.json()["text"] == f"Would archive Issue {stored.id}"
    assert archived.status_code == 204
    assert repository.get(Issue, stored.id) is None
    operation = metamodel_of(bus)["operations"]["tracker.CommandTrashIssue"]
    assert (operation["role"], operation["output"]) == ("archive", {"type": "null"})
