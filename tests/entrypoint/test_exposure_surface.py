"""What a gateway publishes once it names its wire (PRD_14 §2, §5, §7): in DECLARED mode — the
default — only what is bound, so nothing is public by forgetting; in CATALOG mode everything,
logged. One precedence decides every binding: `@internal` · exclude/include · override/bind ·
decorator · group · derived. A gateway that names no wire keeps PRD_12's catalog, unchanged.
"""

from collections.abc import Sequence
from typing import Any

import pytest
from structlog.testing import capture_logs

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.ddd import Query
from sincpro_framework.entrypoints import Exposure, ExposureRefused, Gateway, Group
from sincpro_framework.entrypoints.exposure import (
    Binding,
    Operation,
    Resolved,
    RestBinding,
    RpcBinding,
    Wire,
    internal,
    mcp,
    rest,
)


class RestSurface(Gateway):
    """What `RestGateway` becomes once it opts in: a gateway that names its wire."""

    wire = "rest"


class CommandIssueInvoice(DataTransferObject):
    customer_id: int


class ResponseIssueInvoice(DataTransferObject):
    number: str


class QueryInvoice(Query):
    invoice_id: str


class ResponseInvoice(DataTransferObject):
    number: str


class CommandReconcile(DataTransferObject):
    pass


def _billing(name: str = "billing") -> UseFramework:
    bus = UseFramework(name, log_after_execution=False)

    @bus.feature(CommandIssueInvoice)
    @rest.post("/invoices", status=201, tags=("invoices",))
    @mcp(destructive=False)
    class IssueInvoice(Feature):
        def execute(self, dto: CommandIssueInvoice) -> ResponseIssueInvoice:
            return ResponseIssueInvoice(number="F-1")

    @bus.feature(QueryInvoice)
    class GetInvoice(Feature):
        def execute(self, dto: QueryInvoice) -> ResponseInvoice:
            return ResponseInvoice(number=dto.invoice_id)

    @bus.feature(CommandReconcile)
    @internal
    class Reconcile(Feature):
        def execute(self, dto: CommandReconcile) -> None:
            return None

    return bus


def _commands(gateway: Gateway) -> list[str]:
    return sorted(one.operation.command.__name__ for one in gateway.surface())


def _binding(gateway: Gateway, command: type) -> Any:
    return next(one.binding for one in gateway.surface() if one.operation.command is command)


# --- modes ----------------------------------------------------------------------------------


def test_declared_is_the_default_and_publishes_only_what_is_bound():
    gateway = RestSurface([_billing()], unguarded=True)

    assert gateway.exposure == Exposure.DECLARED
    assert _commands(gateway) == ["CommandIssueInvoice"]
    assert [op.dto for _, _, op in gateway.operations()] == [CommandIssueInvoice]


def test_catalog_publishes_every_use_case_and_logs_each_one():
    with capture_logs() as logs:
        gateway = RestSurface([_billing()], exposure=Exposure.CATALOG, unguarded=True)
        published = _commands(gateway)

    assert published == ["CommandIssueInvoice", "QueryInvoice"]
    said = " ".join(str(one["event"]) for one in logs)
    assert "CommandIssueInvoice published by catalog" in said
    assert "QueryInvoice published by catalog" in said


def test_catalog_lets_a_binding_shape_an_operation():
    gateway = RestSurface([_billing()], exposure=Exposure.CATALOG, unguarded=True)

    assert _binding(gateway, CommandIssueInvoice).status == 201
    assert _binding(gateway, QueryInvoice) == RestBinding()


def test_a_gateway_that_names_no_wire_keeps_the_catalog_of_prd_12():
    """Every wire gateway not yet opted in: all but `@internal`, no access rule, no refusal."""
    gateway = Gateway([_billing()])

    assert sorted(op.name for _, _, op in gateway.operations()) == [
        "CommandIssueInvoice",
        "QueryInvoice",
    ]
    with pytest.raises(ExposureRefused, match="names no wire"):
        gateway.surface()


def test_internal_is_published_by_no_mode():
    gateway = RestSurface([_billing()], exposure=Exposure.CATALOG, unguarded=True)

    assert "CommandReconcile" not in _commands(gateway)


# --- precedence -----------------------------------------------------------------------------


def test_exclude_beats_a_bind():
    gateway = RestSurface(unguarded=True).add(_billing(), exclude=[CommandIssueInvoice])
    gateway.bind(CommandIssueInvoice, RestBinding(method="POST", path="/elsewhere"))

    assert _commands(gateway) == []


def test_include_only_narrows_in_declared_mode():
    gateway = RestSurface(unguarded=True).add(
        _billing(), include=[CommandIssueInvoice, QueryInvoice]
    )

    assert _commands(gateway) == ["CommandIssueInvoice"]


def test_a_catalog_narrowed_after_the_surface_resolved_is_seen():
    """The gateway keeps its resolved surface; a catalog narrowed later must not leave a use
    case published that it no longer lets go."""
    gateway = RestSurface(unguarded=True).add(_billing())
    assert _commands(gateway) == ["CommandIssueInvoice"]

    gateway.catalogs["billing"].exclude(CommandIssueInvoice)

    assert _commands(gateway) == []


def test_bind_publishes_an_unbound_use_case_keyed_by_its_command_or_handler():
    bus = _billing()
    by_command = RestSurface([bus], unguarded=True).bind(
        QueryInvoice, RestBinding(method="GET", path="/invoices/{invoice_id}")
    )
    by_handler = RestSurface([bus], unguarded=True).bind(
        bus.handler_of(QueryInvoice) or QueryInvoice,
        RestBinding(path="/invoices/{invoice_id}"),
    )

    assert _commands(by_command) == ["CommandIssueInvoice", "QueryInvoice"]
    assert _binding(by_handler, QueryInvoice).path == "/invoices/{invoice_id}"


def test_override_merges_field_by_field_over_the_decorator_and_the_last_call_wins():
    gateway = RestSurface([_billing()], unguarded=True)
    gateway.override(CommandIssueInvoice, status=202)
    gateway.override(CommandIssueInvoice, summary="Emitir", status=200)

    binding = _binding(gateway, CommandIssueInvoice)

    assert (binding.method, binding.path) == ("POST", "/invoices")
    assert (binding.status, binding.summary) == (200, "Emitir")


def test_bind_merges_only_the_fields_it_says_over_the_decorator():
    gateway = RestSurface([_billing()], unguarded=True)
    gateway.bind(CommandIssueInvoice, RestBinding(path="/v2/invoices"))

    binding = _binding(gateway, CommandIssueInvoice)

    assert (binding.method, binding.path, binding.status) == ("POST", "/v2/invoices", 201)


def test_override_alone_does_not_publish_in_declared_mode():
    gateway = RestSurface([_billing()], unguarded=True).override(QueryInvoice, status=200)

    with pytest.raises(
        ExposureRefused, match="override shapes a binding, bind publishes one"
    ):
        gateway.surface()


def test_override_and_bind_are_checked_against_the_gateway_wire():
    gateway = RestSurface([_billing()], unguarded=True)

    with pytest.raises(ExposureRefused, match="has no stauts"):
        gateway.override(CommandIssueInvoice, stauts=201)
    with pytest.raises(ExposureRefused, match="bind a RestBinding"):
        gateway.bind(CommandIssueInvoice, RpcBinding(name="billing.issue"))


# --- groups, and a wire that derives --------------------------------------------------------


class CliBinding(Binding):
    wire = "cli"
    command: str | None = None
    tags: tuple[str, ...] = ()


class CliWire(Wire[CliBinding]):
    """A project's own transport: `billing issue-invoice`, the group's prefix before it."""

    binding = CliBinding

    def derive(self, operation: Operation, group: Group) -> CliBinding:
        name = (
            operation.command.__name__.removeprefix("Command").removeprefix("Query").lower()
        )
        return CliBinding(command=f"{group.prefix or group.alias} {name}", tags=group.tags)

    def validate(self, surface: Sequence[Resolved[CliBinding]]) -> list[str]:
        return []

    def build(self, surface: Sequence[Resolved[CliBinding]]) -> dict[str, Any]:
        return {one.binding.command or "": one.operation.run for one in surface}


def _cli_billing() -> UseFramework:
    from sincpro_framework.entrypoints.exposure import declare

    bus = UseFramework("cli-billing", log_after_execution=False)

    class IssueInvoice(Feature):
        def execute(self, dto: CommandIssueInvoice) -> ResponseIssueInvoice:
            return ResponseIssueInvoice(number="F-1")

    class GetInvoice(Feature):
        def execute(self, dto: QueryInvoice) -> ResponseInvoice:
            return ResponseInvoice(number=dto.invoice_id)

    bus.feature(CommandIssueInvoice)(declare(IssueInvoice, CliBinding(tags=("write",))))
    bus.feature(QueryInvoice)(declare(GetInvoice, CliBinding(command="show")))
    return bus


def test_the_group_is_the_context_convention_and_the_binding_wins_on_scalars():
    gateway = Gateway([_cli_billing()], port=CliWire(), unguarded=True)
    gateway.group("cli-billing", prefix="fact", tags=("billing",))

    issue = _binding(gateway, CommandIssueInvoice)
    show = _binding(gateway, QueryInvoice)

    assert issue.command == "fact issueinvoice"
    assert issue.tags == ("billing", "write")
    assert show.command == "show"


def test_a_group_is_data_every_wire_reads():
    bus = _billing()
    gateway = RestSurface({"facturacion": bus}, unguarded=True)
    gateway.group(
        bus, prefix="/billing", tags=("billing",), version="v1", package="billing.v1"
    )

    resolved = gateway.surface()[0]

    assert resolved.group == Group(
        alias="facturacion",
        prefix="/billing",
        tags=("billing",),
        version="v1",
        package="billing.v1",
    )
    assert gateway.groups["facturacion"] == resolved.group


def test_a_group_for_a_bus_not_on_the_gateway_is_refused():
    gateway = RestSurface([_billing()], unguarded=True)

    with pytest.raises(ExposureRefused, match="add it first"):
        gateway.group(_billing("sales"), prefix="/ventas")
