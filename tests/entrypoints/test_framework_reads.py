"""The framework's reads served as reads (PRD_33 D7): a `Get`, a `GetMany` and a `LiteralSearch`
may be bound as `GET` without being a `Query`; a `PreviewEntity` stays `POST` and is still a read.
Every wire publishes the four as reads — MCP read-only, JSON-RPC idempotent, the manifest's
`query`, OpenAPI's `x-sincpro-query`, no `Idempotency-Key` asked —, in line with the metamodel.
Opt-in: a read nobody binds as `GET` is served as it was.
"""

from dataclasses import dataclass
from datetime import timedelta

import pytest

from sincpro_framework import UseFramework
from sincpro_framework.data_layer.caching import Idempotency
from sincpro_framework.data_layer.repositories import MemoryRepository
from sincpro_framework.data_layer.store import InMemoryKeyValue
from sincpro_framework.ddd import (
    Entity,
    EntityReads,
    Get,
    PreviewEntity,
    ResponsePreviewEntity,
    ResponseRecord,
)
from sincpro_framework.entrypoints import McpGateway, RpcGateway
from sincpro_framework.entrypoints.adapters.fastapi.calling import requires_idempotency_key
from sincpro_framework.entrypoints.entrypoint.decorators import mcp, rest, rpc


@dataclass(kw_only=True)
class Issue(Entity):
    title: str = ""


class ResponseIssue(ResponseRecord):
    issue: Issue


class QueryGetIssue(Get[Issue, ResponseIssue]):
    pass


class ResponsePreviewIssue(ResponsePreviewEntity):
    pass


class QueryPreviewIssue(PreviewEntity[Issue, ResponsePreviewIssue]):
    pass


class QueryReadIssue(Get[Issue, ResponseIssue]):
    """A read bound with nothing said of its verb: derived as it always was."""


def tracker() -> tuple[UseFramework, Issue]:
    bus = UseFramework("tracker", log_after_execution=False)
    repository = MemoryRepository()
    bus.add_dependency("repository", repository)
    stored = Issue(title="Printer jammed")
    repository.save(stored)
    once = Idempotency(InMemoryKeyValue()).once(expires_after=timedelta(minutes=5))

    @bus.feature(QueryGetIssue)
    @rest.get("/issues/{id}")
    @rpc()
    @mcp()
    class IssueReads(EntityReads[Issue]):
        pass

    @bus.feature(QueryPreviewIssue)
    @rest.post("/issues/preview")
    @mcp()
    @once
    class IssuePreview(EntityReads[Issue]):
        """Runs once per request — what would ask a write for an `Idempotency-Key`."""

    @bus.feature(QueryReadIssue)
    @rest()
    class IssueReadsDerived(EntityReads[Issue]):
        pass

    return bus, stored


def _fastapi():
    """FastAPI's gateway, or the test skipped where the extra is not installed."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from sincpro_framework.entrypoints import FastApiGateway

    return FastApiGateway, TestClient


def test_a_get_bound_as_get_answers_the_record_read_from_the_url():
    gateway_type, client_type = _fastapi()
    bus, stored = tracker()
    client = client_type(gateway_type([bus], unguarded=True).app())

    answered = client.get(f"/tracker/issues/{stored.id}")

    assert answered.status_code == 200, answered.text
    assert answered.json()["issue"]["title"] == "Printer jammed"


def test_a_get_bound_as_get_is_published_as_a_read_its_criteria_a_json_parameter():
    gateway_type, client_type = _fastapi()
    bus, _ = tracker()
    document = (
        client_type(gateway_type([bus], unguarded=True).app()).get("/openapi.json").json()
    )

    read = document["paths"]["/tracker/issues/{id}"]["get"]
    parameters = {one["name"]: one for one in read["parameters"]}

    assert read["x-sincpro-query"] is True
    assert parameters["id"]["in"] == "path"
    assert "application/json" in parameters["criteria"]["content"]  # as `Search` carries it


def test_a_preview_stays_post_is_published_as_a_read_and_asks_no_idempotency_key():
    gateway_type, client_type = _fastapi()
    bus, _ = tracker()
    client = client_type(gateway_type([bus], unguarded=True).app())
    preview = client.get("/openapi.json").json()["paths"]["/tracker/issues/preview"]["post"]

    answered = client.post("/tracker/issues/preview", json={"values": {"title": "Draft"}})

    assert answered.status_code == 200, answered.text  # no 400 for a missing key
    assert preview["x-sincpro-query"] is True
    assert not requires_idempotency_key(bus, QueryPreviewIssue)
    names = {one["name"] for one in preview.get("parameters", [])}
    assert "Idempotency-Key" not in names


def test_a_read_nobody_binds_as_get_is_served_as_it_was():
    gateway_type, _ = _fastapi()
    bus, _ = tracker()

    derived = {
        entry.command: entry.method
        for entry in gateway_type([bus], unguarded=True).manifest()
    }

    assert derived["tracker.QueryReadIssue"] == "POST"  # no route moves


def test_every_wire_publishes_the_framework_reads_as_reads():
    bus, _ = tracker()
    mcp_gateway = McpGateway([bus], unguarded=True)
    rpc_gateway = RpcGateway([bus], unguarded=True)

    tools = {entry.command: entry.binding for entry in mcp_gateway.manifest()}
    kinds = {entry.command: entry.kind for entry in rpc_gateway.manifest()}

    for read in ("tracker.QueryGetIssue", "tracker.QueryPreviewIssue"):
        assert (tools[read]["read_only"], tools[read]["destructive"]) == (True, False)
    assert kinds["tracker.QueryGetIssue"] == "features.query"


def test_json_rpc_publishes_a_get_as_idempotent():
    from sincpro_framework.entrypoints.adapters.rpc.jrpc import method_object

    bus, _ = tracker()
    [resolved] = [
        one
        for one in RpcGateway([bus], unguarded=True).surface()
        if one.operation.command is QueryGetIssue
    ]

    assert resolved.operation.reads and not resolved.operation.is_query  # no `Query`, a read
    assert method_object("tracker.get_issue", resolved, {})["x-idempotent"] is True
