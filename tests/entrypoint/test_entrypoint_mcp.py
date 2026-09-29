"""The MCP wire on declared exposure (PRD_14 §2, §3, §6; PRD_15 §1.2): what reaches `tools/list`
is only what a use case bound for MCP — or every use case, when the gateway asks for the
catalog — named by its DTO, prefixed only on a clash, with hints derived from facts.

The incidents: a tool published because nobody wrote `@internal`; two contexts answering one
tool name, one shadowing the other; a write advertised as harmless because nobody said it
destroys; a hint read as permission; a tool call that answers differently from the bus.
"""

import asyncio
from collections.abc import Mapping
from datetime import date, timedelta
from typing import Any

import pytest

from sincpro_framework import ApplicationService, DataTransferObject, Feature, UseFramework
from sincpro_framework.auth import AccessControl, Identity, Permission, StaticProvider
from sincpro_framework.caching import Idempotency, InMemoryKeyValue
from sincpro_framework.ddd import Query
from sincpro_framework.entrypoints import ExposureRefused
from sincpro_framework.entrypoints.exposure import Deprecation, Exposure, McpBinding, mcp
from sincpro_framework.entrypoints.mcp import (
    McpGateway,
    McpWire,
    build_mcp_server,
    tool_name_of,
)

fastmcp = pytest.importorskip("fastmcp")


class Perm(Permission):
    ISSUE = "billing.invoice.issue"


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
    once = Idempotency(InMemoryKeyValue()).once(expires_after=timedelta(minutes=5))

    @bus.feature(CommandIssueInvoice)
    @once
    @mcp(title="Emitir factura", destructive=False)
    class IssueInvoice(Feature):
        """Issue an invoice for a customer."""

        def execute(self, dto: CommandIssueInvoice) -> ResponseIssueInvoice:
            return ResponseIssueInvoice(number=f"F-{dto.customer_id}")

    @bus.feature(QueryInvoice)
    @mcp()
    class GetInvoice(Feature):
        """Read one invoice."""

        def execute(self, dto: QueryInvoice) -> ResponseInvoice:
            return ResponseInvoice(number=dto.invoice_id)

    @bus.feature(CommandReconcile)
    class Reconcile(Feature):
        """Declares no MCP binding: never a tool in DECLARED mode."""

        def execute(self, dto: CommandReconcile) -> None:
            return None

    return bus


def _tools(gateway: McpGateway | Any) -> dict[str, Any]:
    server = gateway.server() if isinstance(gateway, McpGateway) else gateway

    async def listed() -> list[Any]:
        async with fastmcp.Client(server) as client:
            return await client.list_tools()

    return {tool.name: tool for tool in asyncio.run(listed())}


def _call(server: Any, name: str, arguments: Mapping[str, Any]) -> Any:
    async def called() -> Any:
        async with fastmcp.Client(server) as client:
            return await client.call_tool(name, dict(arguments))

    return asyncio.run(called())


# Which use cases are tools — DECLARED by default, CATALOG on request


def test_declared_is_the_default_and_publishes_only_what_is_bound_for_mcp() -> None:
    tools = _tools(McpGateway([_billing()], unguarded=True))
    assert set(tools) == {"issue_invoice", "invoice"}


def test_catalog_publishes_every_use_case_of_every_bus() -> None:
    gateway = McpGateway([_billing()], exposure=Exposure.CATALOG, unguarded=True)
    assert set(_tools(gateway)) == {"issue_invoice", "invoice", "reconcile"}
    assert {one.name for one in gateway.manifest()} == {
        "issue_invoice",
        "invoice",
        "reconcile",
    }


def test_an_unguarded_bus_must_be_said_unguarded() -> None:
    with pytest.raises(ExposureRefused, match="has no AccessControl"):
        McpGateway([_billing()]).server()


# Names — the DTO's, snake_case, without Command/Query; overridden; prefixed only on a clash


def test_a_tool_is_named_by_its_dto_without_the_kind() -> None:
    assert tool_name_of(CommandIssueInvoice) == "issue_invoice"
    assert tool_name_of(QueryInvoice) == "invoice"
    assert tool_name_of(ResponseIssueInvoice) == "response_issue_invoice"


def test_a_declared_name_and_title_win_and_reach_tools_list() -> None:
    bus = UseFramework("named", log_after_execution=False)

    @bus.feature(CommandIssueInvoice)
    @mcp("emitir_factura", title="Emitir factura")
    class IssueInvoice(Feature):
        def execute(self, dto: CommandIssueInvoice) -> ResponseIssueInvoice:
            return ResponseIssueInvoice(number="F-1")

    tools = _tools(McpGateway([bus], unguarded=True))
    assert set(tools) == {"emitir_factura"}
    assert tools["emitir_factura"].title == "Emitir factura"
    assert tools["emitir_factura"].annotations.title == "Emitir factura"


def test_the_composition_overrides_a_tool_name() -> None:
    gateway = McpGateway([_billing()], unguarded=True).override(
        CommandIssueInvoice, name="facturar"
    )
    assert set(gateway.tool_names()) == {"facturar", "invoice"}


def test_two_contexts_answering_one_name_are_prefixed_by_their_group_only_there() -> None:
    sales = UseFramework("sales", log_after_execution=False)

    @sales.feature(CommandIssueInvoice)
    @mcp()
    class SalesIssue(Feature):
        def execute(self, dto: CommandIssueInvoice) -> ResponseIssueInvoice:
            return ResponseIssueInvoice(number="V-1")

    gateway = McpGateway([_billing(), sales], unguarded=True).group(sales, prefix="ventas")
    names = gateway.tool_names()
    assert sorted(names) == ["billing_issue_invoice", "invoice", "ventas_issue_invoice"]
    assert {one.name for one in gateway.manifest()} == set(names)
    answered = _call(gateway.server(), "ventas_issue_invoice", {"customer_id": 1})
    assert answered.structured_content == {"number": "V-1"}


def _declaring(bus_name: str, tool: str) -> UseFramework:
    bus = UseFramework(bus_name, log_after_execution=False)

    @bus.feature(QueryInvoice)
    @mcp(tool)
    class Named(Feature):
        def execute(self, dto: QueryInvoice) -> ResponseInvoice:
            return ResponseInvoice(number="x")

    return bus


def test_a_declared_name_twice_is_refused_at_build() -> None:
    """A declared name is the author's: never renamed, so a clash on it is refused."""
    twice = McpGateway(
        [_declaring("one", "facturar"), _declaring("two", "facturar")], unguarded=True
    )
    with pytest.raises(ExposureRefused, match="tool facturar answered by"):
        twice.server()


def test_a_derived_name_yields_to_a_declared_one_by_its_prefix() -> None:
    """Only the derived name is prefixed on a clash — the declared one keeps its name."""
    gateway = McpGateway([_billing(), _declaring("sales", "issue_invoice")], unguarded=True)
    assert sorted(gateway.tool_names()) == [
        "billing_issue_invoice",
        "invoice",
        "issue_invoice",
    ]


def test_one_context_answering_one_name_twice_is_refused() -> None:
    bus = UseFramework("twice", log_after_execution=False)

    class CommandInvoice(DataTransferObject):
        pass

    @bus.feature(CommandInvoice)
    @mcp()
    class Writes(Feature):
        def execute(self, dto: CommandInvoice) -> None:
            return None

    @bus.feature(QueryInvoice)
    @mcp()
    class Reads(Feature):
        def execute(self, dto: QueryInvoice) -> ResponseInvoice:
            return ResponseInvoice(number="x")

    assert any("invoice" in one and "answered by" in one for one in _verify(bus))


def _verify(bus: UseFramework) -> list[str]:
    return McpGateway([bus], unguarded=True).verify()


@pytest.mark.parametrize(
    "name", ["x" * 65, "issue invoice", "facturación", "billing.issue", ""]
)
def test_a_name_no_mcp_client_accepts_is_refused(name: str) -> None:
    gateway = McpGateway([_billing()], unguarded=True).override(
        CommandIssueInvoice, name=name
    )
    assert any("tool name" in one for one in gateway.verify())


def test_a_name_of_64_allowed_characters_is_accepted() -> None:
    name = "a-Z_0" + "x" * 59
    gateway = McpGateway([_billing()], unguarded=True).override(
        CommandIssueInvoice, name=name
    )
    assert gateway.verify() == []
    assert name in gateway.tool_names()


# Hints — derived from facts, declared only where the framework cannot prove them


def _annotations(tools: Mapping[str, Any], name: str) -> dict[str, Any]:
    return tools[name].annotations.model_dump(exclude_none=True)


def test_hints_are_derived_from_the_use_case_and_reach_tools_list() -> None:
    bus = _billing()
    writes = UseFramework("writes", log_after_execution=False)

    class CommandPurge(DataTransferObject):
        pass

    @writes.feature(CommandPurge)
    @mcp(open_world=True)
    class Purge(Feature):
        def execute(self, dto: CommandPurge) -> None:
            return None

    tools = _tools(McpGateway([bus, writes], unguarded=True))
    assert _annotations(tools, "invoice") == {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    }
    assert _annotations(tools, "issue_invoice") == {
        "title": "Emitir factura",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
    assert _annotations(tools, "purge") == {
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
    }


def test_the_manifest_carries_the_resolved_hints() -> None:
    by_name = {one.name: one for one in McpGateway([_billing()], unguarded=True).manifest()}
    assert by_name["invoice"].binding["read_only"] is True
    assert by_name["issue_invoice"].binding["read_only"] is False
    assert by_name["issue_invoice"].binding["destructive"] is False


def test_a_hint_that_contradicts_a_fact_is_refused() -> None:
    bus = UseFramework("contradicts", log_after_execution=False)

    @bus.feature(QueryInvoice)
    @mcp(destructive=True)
    class Reads(Feature):
        def execute(self, dto: QueryInvoice) -> ResponseInvoice:
            return ResponseInvoice(number="x")

    assert any("destructive=True on a Query" in one for one in _verify(bus))


def test_a_deprecated_tool_says_so_first_in_its_description() -> None:
    bus = UseFramework("deprecated", log_after_execution=False)

    @bus.feature(QueryInvoice)
    @mcp(deprecated=Deprecation(sunset=date(2099, 1, 1), replacement=CommandIssueInvoice))
    class Old(Feature):
        """Read one invoice."""

        def execute(self, dto: QueryInvoice) -> ResponseInvoice:
            return ResponseInvoice(number="x")

    description = _tools(McpGateway([bus], unguarded=True))["invoice"].description
    assert description.startswith("DEPRECATED")
    assert "2099-01-01" in description and "issue_invoice" in description
    assert "Read one invoice." in description


# One call path — the tool calls the bus; hints never authorize


def test_a_tool_call_answers_what_the_bus_answers() -> None:
    bus = _billing()
    server = McpGateway([bus], unguarded=True).server()
    through_mcp = _call(server, "issue_invoice", {"customer_id": 7}).structured_content
    through_bus = bus(CommandIssueInvoice(customer_id=7), ResponseIssueInvoice)
    assert through_bus is not None
    assert through_mcp == through_bus.model_dump(mode="json")


ISSUER = Identity.user("user:1", tenant="bo", permissions={Perm.ISSUE})
CLERK = Identity.user("user:2", tenant="bo")


def _guarded() -> UseFramework:
    auth = AccessControl[Perm](
        providers=[StaticProvider({"t-issuer": ISSUER, "t-clerk": CLERK})]
    )
    bus = UseFramework("mcp-guarded", log_after_execution=False)

    @bus.feature(CommandIssueInvoice)
    @auth.requires(Perm.ISSUE)
    @mcp(destructive=False)
    class IssueInvoice(Feature):
        def execute(self, dto: CommandIssueInvoice) -> ResponseIssueInvoice:
            return ResponseIssueInvoice(number="F-1")

    auth.on(bus)
    return bus


class _Request:
    def __init__(self, token: str) -> None:
        self.scope = {
            "type": "http",
            "method": "POST",
            "path": "/mcp",
            "query_string": b"",
            "headers": [(b"authorization", f"Bearer {token}".encode())],
        }


def _as_http_call(monkeypatch: pytest.MonkeyPatch, token: str) -> None:
    from fastmcp.server import dependencies  # pyright: ignore[reportMissingImports]

    monkeypatch.setattr(dependencies, "get_access_token", lambda: None)
    monkeypatch.setattr(dependencies, "get_http_request", lambda: _Request(token))


def test_a_harmless_hint_never_authorizes_the_bus_still_decides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = McpGateway([_guarded()]).server()
    assert _tools(server)["issue_invoice"].annotations.destructiveHint is False
    _as_http_call(monkeypatch, "t-clerk")
    with pytest.raises(fastmcp.exceptions.ToolError):
        _call(server, "issue_invoice", {"customer_id": 1})
    _as_http_call(monkeypatch, "t-issuer")
    assert _call(server, "issue_invoice", {"customer_id": 1}).structured_content == {
        "number": "F-1"
    }


def test_a_guarded_use_case_bound_for_mcp_must_say_who_may_call_it() -> None:
    auth = AccessControl[Perm]()
    bus = UseFramework("mcp-undeclared", log_after_execution=False)

    @bus.feature(CommandIssueInvoice)
    @mcp()
    class IssueInvoice(Feature):
        def execute(self, dto: CommandIssueInvoice) -> ResponseIssueInvoice:
            return ResponseIssueInvoice(number="F-1")

    auth.on(bus)
    assert any("neither @auth.requires nor @auth.public" in one for one in _verify(bus))


# The conveniences stay the catalog, and a project may bring its own wire


def test_build_mcp_server_over_one_bus_keeps_the_catalog_and_its_names() -> None:
    tools = _tools(build_mcp_server(_billing()))
    assert set(tools) == {"CommandIssueInvoice", "QueryInvoice", "CommandReconcile"}
    assert tools["QueryInvoice"].annotations.readOnlyHint is True
    assert tools["CommandIssueInvoice"].annotations.idempotentHint is True


def test_build_mcp_server_over_several_buses_is_the_catalog_of_the_gateway() -> None:
    sales = UseFramework("ventas", log_after_execution=False)

    @sales.app_service(CommandReconcile)
    class Close(ApplicationService):
        def execute(self, dto: CommandReconcile) -> None:
            return None

    tools = _tools(build_mcp_server([_billing(), sales]))
    assert set(tools) == {"issue_invoice", "invoice", "billing_reconcile", "ventas_reconcile"}


def test_a_project_wire_renames_every_tool() -> None:
    class Prefixed(McpWire):
        def derive(self, operation: Any, group: Any) -> McpBinding:
            derived = super().derive(operation, group)
            return derived.model_copy(update={"name": f"sp_{derived.name}"})

    gateway = McpGateway([_billing()], unguarded=True, port=Prefixed())
    assert set(gateway.tool_names()) == {"sp_issue_invoice", "sp_invoice"}


def test_a_write_nobody_said_is_harmless_stays_destructive_whatever_the_wire_derives() -> (
    None
):
    """The MCP default is pessimistic: a port that derives no `destructive` still publishes a
    write as destructive — only a declaration says otherwise."""

    class Undecided(McpWire):
        def derive(self, operation: Any, group: Any) -> McpBinding:
            return McpBinding(
                name=tool_name_of(operation.command), read_only=operation.is_query
            )

    gateway = McpGateway(
        [_billing()], exposure=Exposure.CATALOG, unguarded=True, port=Undecided()
    )
    hints = {tool.name: tool.annotations for tool in gateway.tools()}
    assert hints["reconcile"]["destructiveHint"] is True
    assert hints["issue_invoice"]["destructiveHint"] is False
    assert hints["invoice"]["destructiveHint"] is False
