"""The client contract (PRD_33 §3): per bounded context, one file — the metamodel of what the
gateways expose beside how each wire reaches every operation, joined by the Command's identity —,
the same bytes every time the same code writes it.

A tracker moves issues over JSON-RPC, gRPC and MCP; reading one goes over JSON-RPC alone; a
secret use case is `@internal`. A stock context beside it is a file of its own.
"""

import json
from dataclasses import dataclass
from datetime import date
from typing import Any

import pytest

from sincpro_framework import DataTransferObject, Feature, UseFramework, exceptions
from sincpro_framework.data_layer.repositories import MemoryRepository
from sincpro_framework.ddd import (
    DomainEvent,
    Entity,
    EntityID,
    EntityReads,
    Get,
    ResponseRecord,
)
from sincpro_framework.entrypoints import (
    GrpcGateway,
    McpGateway,
    RpcGateway,
    client_contracts,
    export_client_contract,
)
from sincpro_framework.entrypoints.__main__ import main
from sincpro_framework.entrypoints.domain.bindings import Deprecation
from sincpro_framework.entrypoints.entrypoint.decorators import grpc, internal, mcp, rest, rpc


@dataclass(kw_only=True)
class Issue(Entity):
    title: str = ""


class ResponseIssue(ResponseRecord):
    issue: Issue


class QueryGetIssue(Get[Issue, ResponseIssue]):
    pass


class CommandMoveIssue(DataTransferObject):
    issue_id: EntityID[Issue]
    destination: str


class CommandSecret(DataTransferObject):
    reason: str


@dataclass(kw_only=True)
class Shelf(Entity):
    code: str = ""


class CommandCountShelf(DataTransferObject):
    shelf_id: EntityID[Shelf]


SUNSET = Deprecation(sunset=date(2027, 1, 1), replacement=QueryGetIssue)


def tracker() -> UseFramework:
    bus = UseFramework("tracker", log_after_execution=False)
    bus.add_dependency("repository", MemoryRepository())

    @bus.feature(QueryGetIssue)
    @rest.post("/issues/{id}/read")
    @rpc()
    class IssueReads(EntityReads[Issue]):
        pass

    @bus.feature(CommandMoveIssue)
    @rest.put("/issues/{issue_id}/move", deprecated=SUNSET)
    @rpc(deprecated=SUNSET)
    @grpc(deprecated=SUNSET)
    @mcp(deprecated=SUNSET)
    class MoveIssue(Feature):
        def execute(self, dto: CommandMoveIssue) -> None:
            return None

    @bus.feature(CommandSecret)
    @internal
    class Secret(Feature):
        def execute(self, dto: CommandSecret) -> None:
            return None

    return bus


def stock() -> UseFramework:
    bus = UseFramework("stock", log_after_execution=False)

    @bus.feature(CommandCountShelf)
    @rpc()
    class CountShelf(Feature):
        def execute(self, dto: CommandCountShelf) -> None:
            return None

    return bus


def wires(*buses: UseFramework) -> list[Any]:
    """One gateway per wire over `buses` — the ones that need no extra to list their surface."""
    return [
        RpcGateway(list(buses), unguarded=True),
        GrpcGateway(list(buses), unguarded=True),
        McpGateway(list(buses), unguarded=True),
    ]


EXPORTED = wires(tracker(), stock())
"""What the command line exports in `test_the_command_line_writes_what_the_function_does`."""


@dataclass(kw_only=True)
class IssueMoved(DomainEvent):
    """An issue went to another project."""

    name = "tracker.issue.v1.moved"
    issue_id: str


def tracker_hearing_moves() -> UseFramework:
    """The tracker, reacting to its own moves — a subscriber no wire exposes."""
    bus = tracker()

    @bus.feature(IssueMoved)
    class NoteMove(Feature):
        def execute(self, dto: IssueMoved) -> None:
            return None

    return bus


EXPORTED_WITH_EVENTS = wires(tracker_hearing_moves())
"""What the command line exports with `--events`."""


def _without(contract: dict[str, Any], *parts: str) -> dict[str, Any]:
    """The contract without `parts`, to compare what is left."""
    return {name: value for name, value in contract.items() if name not in parts}


# ---------------------------------------------------------------------------------------------
# What a contract holds
# ---------------------------------------------------------------------------------------------


def test_a_contract_is_the_metamodel_beside_each_wires_bindings_by_one_identity():
    contract = client_contracts(wires(tracker()))["tracker"]

    assert contract["format"] == "sincpro.client-contract/1"
    assert contract["bounded_context"] == "tracker"
    assert sorted(contract["operations"]) == [
        "tracker.CommandMoveIssue",
        "tracker.QueryGetIssue",
    ]
    assert contract["operations"]["tracker.QueryGetIssue"]["role"] == "get"
    assert contract["bindings"]["rpc"] == {
        "tracker.CommandMoveIssue": {
            "method": "tracker.move_issue",
            "deprecated": True,
            "sunset": "2027-01-01",
            "replacement": "tracker.QueryGetIssue",
        },
        "tracker.QueryGetIssue": {"method": "tracker.get_issue"},
    }
    assert contract["bindings"]["grpc"]["tracker.CommandMoveIssue"]["method"] == (
        "/tracker.v1.TrackerService/MoveIssue"
    )
    assert contract["bindings"]["mcp"]["tracker.CommandMoveIssue"]["tool"] == "move_issue"
    for bindings in contract["bindings"].values():
        assert set(bindings) <= set(contract["operations"])


def test_a_wire_that_does_not_publish_an_operation_has_no_binding_for_it():
    bindings = client_contracts(wires(tracker()))["tracker"]["bindings"]

    assert "tracker.QueryGetIssue" in bindings["rpc"]
    assert "tracker.QueryGetIssue" not in bindings["grpc"]
    assert "tracker.QueryGetIssue" not in bindings["mcp"]


def test_an_internal_use_case_is_in_no_contract():
    contract = client_contracts(wires(tracker()))["tracker"]

    assert "tracker.CommandSecret" not in contract["operations"]
    assert not any("tracker.CommandSecret" in one for one in contract["bindings"].values())


def test_a_deprecation_every_wire_says_alike_is_the_operations_own():
    operations = client_contracts(wires(tracker()))["tracker"]["operations"]

    moved, read = operations["tracker.CommandMoveIssue"], operations["tracker.QueryGetIssue"]
    assert (moved["deprecated"], moved["sunset"], moved["replacement"]) == (
        True,
        "2027-01-01",
        "tracker.QueryGetIssue",
    )
    assert (read["deprecated"], read["sunset"], read["replacement"]) == (False, None, None)


def test_each_bounded_context_is_a_file_of_its_own(tmp_path):
    written = export_client_contract(wires(tracker(), stock()), tmp_path)

    assert written == {
        "stock": tmp_path / "stock.contract.json",
        "tracker": tmp_path / "tracker.contract.json",
    }
    stocked = json.loads(written["stock"].read_text())
    assert list(stocked["operations"]) == ["stock.CommandCountShelf"]
    assert list(stocked["models"]) == ["Shelf"]


# ---------------------------------------------------------------------------------------------
# The same code, the same bytes — and a change shows where it is
# ---------------------------------------------------------------------------------------------


def test_the_same_gateways_write_the_same_bytes(tmp_path):
    first = export_client_contract(wires(tracker()), tmp_path / "first")["tracker"]
    second = export_client_contract(wires(tracker()), tmp_path / "second")["tracker"]

    assert first.read_bytes() == second.read_bytes()
    assert first.read_text().endswith("}\n")
    assert json.loads(first.read_text()) == client_contracts(wires(tracker()))["tracker"]


def _issues_with(field: str | None) -> UseFramework:
    """A tracker whose `Issue` holds `field` beside its title — the same classes otherwise."""

    @dataclass(kw_only=True)
    class Issue(Entity):
        title: str = ""

    if field is not None:
        Issue = dataclass(kw_only=True)(  # noqa: N806 — the class a newer release ships
            type("Issue", (Issue,), {"__annotations__": {field: str}, field: ""})
        )

    class ResponseIssue(ResponseRecord):
        issue: Issue  # type: ignore[valid-type]

    class QueryGetIssue(Get[Issue, ResponseIssue]):  # type: ignore[valid-type]
        pass

    bus = UseFramework("tracker", log_after_execution=False)
    bus.add_dependency("repository", MemoryRepository())

    @bus.feature(QueryGetIssue)
    @rpc()
    class IssueReads(EntityReads[Issue]):  # type: ignore[valid-type]
        pass

    return bus


def test_adding_a_field_changes_only_the_models_and_the_version():
    before = client_contracts(RpcGateway([_issues_with(None)], unguarded=True))["tracker"]
    after = client_contracts(RpcGateway([_issues_with("priority")], unguarded=True))[
        "tracker"
    ]

    assert "priority" in after["models"]["Issue"]["properties"]
    assert before["models"] != after["models"] and before["version"] != after["version"]
    assert _without(before, "models", "version") == _without(after, "models", "version")


# ---------------------------------------------------------------------------------------------
# REST: the full path and the fields it takes
# ---------------------------------------------------------------------------------------------


def test_a_rest_binding_names_the_full_path_and_the_fields_its_names_take():
    pytest.importorskip("fastapi")
    from sincpro_framework.entrypoints import FastApiGateway

    bus = tracker()
    gateway = FastApiGateway([bus], unguarded=True).group(
        bus, prefix="/tracker", version="v2"
    )

    rest_bindings = client_contracts(gateway)["tracker"]["bindings"]["rest"]

    assert rest_bindings["tracker.CommandMoveIssue"] == {
        "method": "PUT",
        "path": "/v2/tracker/issues/{issue_id}/move",
        "path_fields": ["issue_id"],
        "deprecated": True,
        "sunset": "2027-01-01",
        "replacement": "tracker.QueryGetIssue",
    }
    for binding in rest_bindings.values():
        named = {f"{{{one}}}" for one in binding["path_fields"]}
        assert {part for part in binding["path"].split("/") if part.startswith("{")} == named


def test_changing_a_route_changes_only_the_bindings():
    pytest.importorskip("fastapi")
    from sincpro_framework.entrypoints import FastApiGateway

    before = client_contracts(FastApiGateway([tracker()], unguarded=True))["tracker"]
    after = client_contracts(
        FastApiGateway([tracker()], unguarded=True).override(
            CommandMoveIssue, path="/issues/{issue_id}/relocate"
        )
    )["tracker"]

    assert after["bindings"]["rest"]["tracker.CommandMoveIssue"]["path"] == (
        "/tracker/issues/{issue_id}/relocate"
    )
    assert _without(before, "bindings", "version") == _without(after, "bindings", "version")


# ---------------------------------------------------------------------------------------------
# The command line: nothing served
# ---------------------------------------------------------------------------------------------


def test_the_command_line_writes_what_the_function_does(tmp_path, capsys):
    exit_code = main(
        [
            "export-client-contract",
            "tests.entrypoints.test_client_contract:EXPORTED",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "stock.contract.json",
        "tracker.contract.json",
    ]
    assert capsys.readouterr().out.splitlines() == [
        f"stock: {tmp_path / 'stock.contract.json'}",
        f"tracker: {tmp_path / 'tracker.contract.json'}",
    ]


def test_the_command_line_adds_the_events_only_when_asked(tmp_path, capsys):
    plain, told = tmp_path / "plain", tmp_path / "told"
    target = "tests.entrypoints.test_client_contract:EXPORTED_WITH_EVENTS"

    assert main(["export-client-contract", target, str(plain)]) == 0
    assert main(["export-client-contract", target, str(told), "--events"]) == 0

    assert "events" not in json.loads((plain / "tracker.contract.json").read_text())
    events = json.loads((told / "tracker.contract.json").read_text())["events"]
    assert list(events) == ["tracker.issue.v1.moved"]


# ---------------------------------------------------------------------------------------------
# Domain events, only when asked
# ---------------------------------------------------------------------------------------------


def test_a_contract_holds_the_contexts_events_only_when_asked_and_of_the_whole_bus(tmp_path):
    gateways = wires(tracker_hearing_moves())

    [plain] = client_contracts(gateways).values()
    [told] = client_contracts(gateways, events=True).values()
    written = export_client_contract(gateways, tmp_path, events=True)

    assert "events" not in plain
    assert _without(told, "events", "version") == _without(plain, "version")
    assert told["version"] != plain["version"]
    moved = told["events"]["tracker.issue.v1.moved"]
    assert (moved["title"], moved["handled"], moved["recorded"]) == (
        "IssueMoved",
        True,
        False,
    )
    assert "tracker.IssueMoved" not in told["operations"]  # no client calls it
    assert not any("IssueMoved" in one for wire in told["bindings"].values() for one in wire)
    assert json.loads(written["tracker"].read_text()) == told


def test_a_subscriber_changes_nothing_in_a_contract_not_asked_for_events():
    gateways = wires(tracker())

    assert client_contracts(gateways) == client_contracts(gateways, events=False)
    assert client_contracts(gateways) == client_contracts(wires(tracker_hearing_moves()))


# ---------------------------------------------------------------------------------------------
# The context's own name, and the alias a client names it by
# ---------------------------------------------------------------------------------------------


def test_a_contract_says_the_contexts_own_name_and_the_alias_it_is_published_under(tmp_path):
    bus = tracker()  # named "tracker"; published as "issues", as forge publishes "project"
    gateways = [RpcGateway({"issues": bus}, unguarded=True)]

    [contract] = client_contracts(gateways).values()
    written = export_client_contract(gateways, tmp_path)

    assert (contract["bounded_context"], contract["alias"]) == ("tracker", "issues")
    assert list(contract["operations"])[0].startswith("tracker.")  # identities keep the name
    assert written == {"issues": tmp_path / "issues.contract.json"}


def test_an_alias_that_names_two_bounded_contexts_is_refused():
    gateways = [
        RpcGateway({"desk": tracker()}, unguarded=True),
        RpcGateway({"desk": stock()}, unguarded=True),
    ]

    with pytest.raises(
        exceptions.ProgrammingError, match="an alias names one bounded context"
    ):
        client_contracts(gateways)
