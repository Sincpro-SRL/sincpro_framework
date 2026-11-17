"""A list grouped through MCP: the agent sends the same `Search` tool a criteria with `grouping`
and reads the groups back — each one's value, count, measures and the criteria that opens it —,
and the tool's own schema tells it so before it asks.
"""

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest

from sincpro_framework import UseFramework
from sincpro_framework.data_layer.repositories import MemoryRepository
from sincpro_framework.ddd import Entity, EntityReads, ResponsePaginatedQuery, Search
from sincpro_framework.entrypoints import McpGateway
from sincpro_framework.entrypoints.domain.surface import Exposure

fastmcp = pytest.importorskip("fastmcp")


@dataclass(kw_only=True)
class Fee(Entity):
    state: str = ""
    amount: int = 0


class ResponseFees(ResponsePaginatedQuery):
    fees: list[Fee]


class QueryListFees(Search[Fee, ResponseFees]):
    pass


def _server() -> Any:
    bus = UseFramework("dues", log_after_execution=False)
    repository = MemoryRepository()
    repository.save(
        [Fee(state="paid", amount=n) for n in range(3)] + [Fee(state="due", amount=9)]
    )
    bus.add_dependency("repository", repository)

    @bus.feature(QueryListFees)
    class FeeReads(EntityReads[Fee]):
        pass

    return McpGateway([bus], exposure=Exposure.CATALOG, unguarded=True).server()


def _tool(server: Any) -> Any:
    async def listed() -> list[Any]:
        async with fastmcp.Client(server) as client:
            return await client.list_tools()

    return next(one for one in asyncio.run(listed()) if "fees" in one.name.lower())


def _called(server: Any, name: str, arguments: dict[str, Any]) -> Any:
    async def called() -> Any:
        async with fastmcp.Client(server) as client:
            return await client.call_tool(name, arguments)

    return asyncio.run(called())


def test_the_search_tool_answers_the_groups_its_criteria_asks_for():
    server = _server()
    tool = _tool(server)
    criteria = {
        "grouping": {
            "group_by": [{"field": "state"}],
            "measures": {"total": {"function": "sum", "field": "amount"}},
        }
    }

    answered = _called(server, tool.name, {"criteria": criteria}).structured_content

    groups = {one["value"]: one for one in answered["groups"]}
    assert {value: one["count"] for value, one in groups.items()} == {"paid": 3, "due": 1}
    assert groups["paid"]["measures"] == {"total": 3}
    assert groups["due"]["criteria"]["selection"]["value"] == "due"


def test_the_tool_tells_the_agent_a_grouping_is_answered_in_groups():
    """What the agent reads before it asks: the criteria's `grouping` and what comes back."""
    schema = _tool(_server()).inputSchema

    told = " ".join(
        schema["$defs"]["Criteria"]["properties"]["grouping"]["description"].split()
    )
    assert "A search answers them in `groups`" in told
