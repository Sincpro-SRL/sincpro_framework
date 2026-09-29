"""The surface as data, and a wire of the project's (PRD_14 §8, §9). The manifest is what a CI
snapshot diffs, so any change to the public surface shows in the pull request; the `Wire` port is
what a project's own transport implements to get decorators, groups, precedence and validation
for free — and it still calls the bus, so access and stages are never skipped.
"""

import json
from collections.abc import Sequence
from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.auth import AccessControl, Permission
from sincpro_framework.auth.domain import Unauthenticated
from sincpro_framework.ddd import Query
from sincpro_framework.entrypoints import ExposureRefused, Gateway, Group
from sincpro_framework.entrypoints.exposure import (
    Binding,
    Deprecation,
    Operation,
    Resolved,
    Wire,
    declare,
    mcp,
    rest,
)


class BillingPermission(Permission):
    ISSUE = "billing.invoice.issue"


class CommandIssueInvoice(DataTransferObject):
    customer_id: int


class ResponseIssueInvoice(DataTransferObject):
    number: str


class QueryInvoice(Query):
    invoice_id: str


class ResponseInvoice(DataTransferObject):
    number: str


class RestSurface(Gateway):
    wire = "rest"


def _guarded_billing() -> tuple[UseFramework, AccessControl[BillingPermission]]:
    auth = AccessControl[BillingPermission]()
    bus = UseFramework("manifest-billing", log_after_execution=False)

    @bus.feature(CommandIssueInvoice)
    @auth.requires(BillingPermission.ISSUE)
    @rest.post("/invoices", status=201, tags=("invoices",))
    @mcp(destructive=False)
    class IssueInvoice(Feature):
        def execute(self, dto: CommandIssueInvoice) -> ResponseIssueInvoice:
            return ResponseIssueInvoice(number="F-1")

    @bus.feature(QueryInvoice)
    @auth.public
    @rest.get(
        "/invoices/{invoice_id}",
        deprecated=Deprecation(
            since=date(2026, 1, 1), sunset=date(2099, 1, 1), replacement=CommandIssueInvoice
        ),
    )
    class GetInvoice(Feature):
        def execute(self, dto: QueryInvoice) -> ResponseInvoice:
            return ResponseInvoice(number=dto.invoice_id)

    auth.on(bus)
    return bus, auth


def test_the_manifest_is_the_surface_as_frozen_json_safe_data_in_a_stable_order():
    bus, _ = _guarded_billing()
    manifest = RestSurface({"billing": bus}).manifest()

    snapshot = [entry.model_dump(mode="json") for entry in manifest]

    assert json.loads(json.dumps(snapshot)) == snapshot
    assert snapshot == [
        {
            "wire": "rest",
            "context": "billing",
            "command": f"{__name__}.CommandIssueInvoice",
            "name": "CommandIssueInvoice",
            "kind": "features.command",
            "method": "POST",
            "access": "billing.invoice.issue",
            "deprecated": False,
            "sunset": None,
            "replacement": None,
            "binding": {
                "method": "POST",
                "path": "/invoices",
                "status": 201,
                "location": None,
                "tags": ["invoices"],
                "summary": None,
                "responses": [],
                "body": None,
                "concurrency": None,
            },
        },
        {
            "wire": "rest",
            "context": "billing",
            "command": f"{__name__}.QueryInvoice",
            "name": "QueryInvoice",
            "kind": "features.query",
            "method": "GET",
            "access": "public",
            "deprecated": True,
            "sunset": "2099-01-01",
            "replacement": f"{__name__}.CommandIssueInvoice",
            "binding": {
                "method": "GET",
                "path": "/invoices/{invoice_id}",
                "status": None,
                "location": None,
                "tags": [],
                "summary": None,
                "responses": [],
                "body": None,
                "concurrency": None,
            },
        },
    ]
    with pytest.raises(ValidationError):
        manifest[0].name = "renamed"  # type: ignore[misc]


def test_an_unguarded_surface_says_so_in_its_manifest():
    bus = UseFramework("unguarded-manifest", log_after_execution=False)

    @bus.feature(CommandIssueInvoice)
    @rest.post("/invoices")
    class IssueInvoice(Feature):
        def execute(self, dto: CommandIssueInvoice) -> ResponseIssueInvoice:
            return ResponseIssueInvoice(number="F-1")

    (entry,) = RestSurface([bus], unguarded=True).manifest()

    assert entry.access == "unguarded"


# --- a wire of the project's ----------------------------------------------------------------


class CliBinding(Binding):
    wire = "cli"
    command: str | None = None


class Cli:
    """What the CLI wire builds: a table of commands, each calling the bus."""

    def __init__(self, table: dict[str, Any]) -> None:
        self.table = table

    def __call__(self, line: str, **payload: Any) -> Any:
        return self.table[line](payload)


class CliWire(Wire[CliBinding]):
    binding = CliBinding

    def derive(self, operation: Operation, group: Group) -> CliBinding:
        verb = operation.command.__name__.removeprefix("Command").removeprefix("Query")
        return CliBinding(command=f"{group.prefix or group.alias} {verb.lower()}")

    def name_of(self, resolved: Resolved[CliBinding]) -> str:
        return resolved.binding.command or ""

    def validate(self, surface: Sequence[Resolved[CliBinding]]) -> list[str]:
        seen: dict[str, str] = {}
        problems = []
        for one in surface:
            line = self.name_of(one)
            if line in seen:
                problems.append(
                    f"cli: '{line}' answered by {seen[line]} and "
                    f"{one.operation.command.__name__}"
                )
            seen[line] = one.operation.command.__name__
        return problems

    def build(self, surface: Sequence[Resolved[CliBinding]]) -> Cli:
        return Cli({self.name_of(one): one.operation.run for one in surface})


def _cli_billing() -> UseFramework:
    bus, _ = _guarded_billing()
    declare(bus.handler_of(CommandIssueInvoice) or CommandIssueInvoice, CliBinding())
    declare(bus.handler_of(QueryInvoice) or QueryInvoice, CliBinding(command="billing show"))
    return bus


def test_a_project_wire_is_resolved_validated_and_built_by_the_gateway():
    gateway = Gateway([_cli_billing()], port=CliWire()).group(
        "manifest-billing", prefix="fact"
    )

    cli = gateway.build()

    assert sorted(cli.table) == ["billing show", "fact issueinvoice"]
    assert cli("billing show", invoice_id="F-9") == {"number": "F-9"}
    assert [entry.name for entry in gateway.manifest()] == [
        "billing show",
        "fact issueinvoice",
    ]


def test_a_project_wire_still_runs_through_the_access_guard():
    gateway = Gateway([_cli_billing()], port=CliWire())

    with pytest.raises(Unauthenticated):
        gateway.build()("manifest-billing issueinvoice", customer_id=1)


def test_a_project_wire_refuses_its_own_clashes_through_the_build():
    gateway = Gateway([_cli_billing()], port=CliWire()).override(
        CommandIssueInvoice, command="billing show"
    )

    with pytest.raises(ExposureRefused, match="'billing show' answered by"):
        gateway.build()
