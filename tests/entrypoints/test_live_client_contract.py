"""The client contract served live (PRD_33 phase 3): every HTTP gateway answers each bounded
context's contract at `/client-contract/<context>.json` — the bytes the export writes —, tagged by
its version and `304` to a client that holds it; a use case registered while it runs changes the
document and its tag with no restart.
"""

from dataclasses import dataclass

import pytest
from starlette.testclient import TestClient

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.ddd import Entity, EntityID
from sincpro_framework.entrypoints import RpcGateway, export_client_contract
from sincpro_framework.entrypoints.entrypoint.decorators import rpc


@dataclass(kw_only=True)
class Issue(Entity):
    title: str = ""


class CommandCloseIssue(DataTransferObject):
    issue_id: EntityID[Issue]


class CommandReopenIssue(DataTransferObject):
    issue_id: EntityID[Issue]


def tracker() -> UseFramework:
    bus = UseFramework("tracker", log_after_execution=False)

    @bus.feature(CommandCloseIssue)
    @rpc()
    class CloseIssue(Feature):
        def execute(self, dto: CommandCloseIssue) -> None:
            return None

    return bus


def test_the_served_contract_is_byte_for_byte_what_the_export_writes(tmp_path):
    gateway = RpcGateway([tracker()], unguarded=True)
    client = TestClient(gateway.app())

    served = client.get("/client-contract/tracker.json")
    written = export_client_contract(gateway, tmp_path)["tracker"]

    assert served.status_code == 200
    assert served.headers["content-type"].startswith("application/json")
    assert served.content == written.read_bytes()
    assert served.headers["etag"] == f'"{served.json()["version"]}"'


def test_a_client_that_holds_the_contract_is_answered_304():
    client = TestClient(RpcGateway([tracker()], unguarded=True).app())
    first = client.get("/client-contract/tracker.json")

    again = client.get(
        "/client-contract/tracker.json", headers={"If-None-Match": first.headers["etag"]}
    )

    assert (again.status_code, again.content, again.headers["etag"]) == (
        304,
        b"",
        first.headers["etag"],
    )


def test_a_use_case_registered_while_it_runs_changes_the_contract_and_its_tag():
    bus = tracker()
    client = TestClient(RpcGateway([bus], unguarded=True).app())
    before = client.get("/client-contract/tracker.json")

    @bus.feature(CommandReopenIssue)
    @rpc()
    class ReopenIssue(Feature):
        def execute(self, dto: CommandReopenIssue) -> None:
            return None

    after = client.get(
        "/client-contract/tracker.json", headers={"If-None-Match": before.headers["etag"]}
    )

    assert after.status_code == 200  # the tag it held is no longer the contract's
    assert after.headers["etag"] != before.headers["etag"]
    assert "tracker.CommandReopenIssue" in after.json()["operations"]
    assert "tracker.CommandReopenIssue" not in before.json()["operations"]


def test_a_context_it_does_not_serve_is_a_404_problem_and_a_none_path_serves_nothing():
    gateway = RpcGateway([tracker()], unguarded=True)

    missing = TestClient(gateway.app()).get("/client-contract/nowhere.json")
    unserved = TestClient(gateway.app(contract_path=None)).get(
        "/client-contract/tracker.json"
    )

    assert missing.status_code == 404
    assert missing.headers["content-type"].startswith("application/problem+json")
    assert unserved.status_code == 404


def test_the_fastapi_gateway_serves_it_too_out_of_its_openapi_document(tmp_path):
    pytest.importorskip("fastapi")
    from sincpro_framework.entrypoints import FastApiGateway
    from sincpro_framework.entrypoints.entrypoint.decorators import rest

    bus = UseFramework("desk", log_after_execution=False)

    @bus.feature(CommandCloseIssue)
    @rest.post("/issues/{issue_id}/close")
    class CloseIssue(Feature):
        def execute(self, dto: CommandCloseIssue) -> None:
            return None

    gateway = FastApiGateway([bus], unguarded=True)
    client = TestClient(gateway.app())

    served = client.get("/client-contract/desk.json")

    assert served.status_code == 200
    assert served.content == export_client_contract(gateway, tmp_path)["desk"].read_bytes()
    assert served.json()["bindings"]["rest"]["desk.CommandCloseIssue"]["path"] == (
        "/desk/issues/{issue_id}/close"
    )
    assert not any(
        "client-contract" in path for path in client.get("/openapi.json").json()["paths"]
    )


def test_the_live_contract_is_named_by_the_alias_the_gateway_publishes():
    client = TestClient(RpcGateway({"issues": tracker()}, unguarded=True).app())

    served = client.get("/client-contract/issues.json")

    assert served.status_code == 200
    assert (served.json()["alias"], served.json()["bounded_context"]) == ("issues", "tracker")
    assert client.get("/client-contract/tracker.json").status_code == 404
