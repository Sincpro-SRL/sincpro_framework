"""Both eras of the MCP protocol on one server the framework builds — a client that opens with
`initialize` (2025-11-25 and before) and one that sends its version on every request (2026-07-28,
`server/discover`, no handshake) list and call the same tools, as the same caller, refused the
same way, on replicas behind a balancer that keeps no affinity.

The incident: a server on FastMCP 3 answered every 2026-07-28 request with 400 "Unsupported
protocol version"; Claude fell back to `initialize`, and on each re-probe could be left with no
tools. A client that speaks only the new era never reached the tools at all.
"""

import asyncio
import itertools
import socket
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest

from sincpro_framework import DataTransferObject, Feature, UseFramework
from sincpro_framework.auth import (
    AccessControl,
    Identity,
    Permission,
    StaticProvider,
    security,
)
from sincpro_framework.entrypoints import McpGateway
from sincpro_framework.entrypoints.entrypoint.decorators import mcp

fastmcp = pytest.importorskip("fastmcp", minversion="4")
httpx2 = pytest.importorskip("httpx2")
uvicorn = pytest.importorskip("uvicorn")
mcp_exceptions = pytest.importorskip("mcp.shared.exceptions")

LEGACY = "legacy"
MODERN = "2026-07-28"


class Perm(Permission):
    ISSUE = "billing.invoice.issue"


class CommandIssueInvoice(DataTransferObject):
    customer_id: int


class ResponseIssueInvoice(DataTransferObject):
    number: str
    by: str


ISSUER = Identity.user("user:1", tenant="bo", permissions={Perm.ISSUE})
CLERK = Identity.user("user:2", tenant="bo")


def _guarded() -> tuple[UseFramework, AccessControl[Perm]]:
    access = AccessControl[Perm](
        providers=[StaticProvider({"t-issuer": ISSUER, "t-clerk": CLERK})]
    )
    billing = UseFramework("eras-billing", log_after_execution=False)

    @billing.feature(CommandIssueInvoice)
    @access.requires(Perm.ISSUE)
    @mcp(destructive=False)
    class IssueInvoice(Feature):
        """Issue an invoice for a customer."""

        def execute(self, dto: CommandIssueInvoice) -> ResponseIssueInvoice:
            return ResponseIssueInvoice(
                number=f"F-{dto.customer_id}", by=security.identity.subject
            )

    access.on(billing)
    return billing, access


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _host(app: Any) -> Iterator[int]:
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.01)
    try:
        yield port
    finally:
        server.should_exit = True
        thread.join(5)


@pytest.fixture(scope="module")
def replicas() -> Iterator[list[int]]:
    """Two replicas of one stateless server — what a deployment with N pods runs."""
    billing, access = _guarded()
    gateway = McpGateway([billing])
    first = _host(gateway.server(auth=access).http_app(stateless_http=True))
    second = _host(gateway.server(auth=access).http_app(stateless_http=True))
    yield [next(first), next(second)]
    for hosted in (first, second):
        next(hosted, None)


class Balancer(httpx2.AsyncBaseTransport):
    """A load balancer with no affinity: each request to the next replica, in turn — and what
    each answered, with the protocol version the request said."""

    def __init__(self, ports: list[int]) -> None:
        self.ports = itertools.cycle(ports)
        self.forward = httpx2.AsyncHTTPTransport()
        self.answered: list[tuple[int, int, str | None]] = []

    async def handle_async_request(self, request: Any) -> Any:
        port = next(self.ports)
        request.url = request.url.copy_with(host="127.0.0.1", port=port)
        response = await self.forward.handle_async_request(request)
        self.answered.append(
            (port, response.status_code, request.headers.get("mcp-protocol-version"))
        )
        return response


def _session(mode: str, token: str, balancer: Balancer) -> dict[str, Any]:
    """One client in `mode`, as the holder of `token`, through `balancer`: the version it
    negotiated, the tools it listed and what issuing an invoice answered."""

    def through_balancer(**options: Any) -> Any:
        return httpx2.AsyncClient(transport=balancer, **options)

    transport = fastmcp.client.transports.StreamableHttpTransport(
        "http://replicas/mcp", auth=token, httpx_client_factory=through_balancer
    )

    async def run() -> dict[str, Any]:
        async with fastmcp.Client(transport, mode=mode) as client:
            tools = [tool.name for tool in await client.list_tools()]
            issued = await client.call_tool(
                "issue_invoice", {"customer_id": 7}, raise_on_error=False
            )
            return {
                "protocol": client.protocol_version,
                "tools": tools,
                "is_error": issued.is_error,
                "answer": issued.structured_content,
            }

    return asyncio.run(run())


@pytest.mark.parametrize(
    ("mode", "negotiated"),
    [(LEGACY, "2025-11-25"), (MODERN, MODERN), ("auto", MODERN)],
)
def test_each_era_lists_and_calls_the_same_tools_as_the_same_caller(
    replicas: list[int], mode: str, negotiated: str
) -> None:
    balancer = Balancer(replicas)
    said = _session(mode, "t-issuer", balancer)

    assert (said["protocol"], said["tools"], said["is_error"]) == (
        negotiated,
        ["issue_invoice"],
        False,
    )
    assert said["answer"] == {"number": "F-7", "by": "user:1"}
    assert {port for port, _, _ in balancer.answered} == set(replicas)


@pytest.mark.parametrize("mode", [MODERN, "auto"])
def test_a_modern_client_is_never_answered_400_nor_falls_back_to_the_handshake(
    replicas: list[int], mode: str
) -> None:
    balancer = Balancer(replicas)
    _session(mode, "t-issuer", balancer)

    assert [status for _, status, _ in balancer.answered if status >= 400] == []
    assert {version for _, _, version in balancer.answered} == {MODERN}


@pytest.mark.parametrize("mode", [LEGACY, MODERN])
def test_each_era_refuses_a_caller_without_the_permission_with_its_problem(
    replicas: list[int], mode: str
) -> None:
    said = _session(mode, "t-clerk", Balancer(replicas))

    assert said["is_error"]
    assert (said["answer"]["kind"], said["answer"]["next_step"]) == (
        "permission_denied",
        "ask_for_access",
    )


@pytest.mark.parametrize("mode", [LEGACY, MODERN])
def test_each_era_refuses_an_unknown_bearer_with_401(replicas: list[int], mode: str) -> None:
    balancer = Balancer(replicas)
    with pytest.raises(mcp_exceptions.MCPError):
        _session(mode, "stolen", balancer)

    assert [status for _, status, _ in balancer.answered] == [401]
