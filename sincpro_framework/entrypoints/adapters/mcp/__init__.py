"""The MCP wire: use cases as tools of a FastMCP 3 server, every call through the bus.

    from sincpro_framework.entrypoints.adapters.mcp import McpGateway, build_mcp_server

    McpGateway([billing, sales]).run()             # the @mcp()-declared tools, stdio
    McpGateway([billing]).run(transport="http")    # MCP Streamable HTTP at /mcp
    build_mcp_server(billing)                      # the whole catalog, no declaration
    McpGateway([billing]).tool_names()      ->  ["issue_invoice", "invoice"]

Importing it needs no `[mcp]` extra: FastMCP is imported only when a server is built.
"""

from sincpro_framework.entrypoints.adapters.mcp.entrypoint import (
    Entrypoint,
    McpGateway,
    build_mcp_server,
)
from sincpro_framework.entrypoints.adapters.mcp.wire import McpTool, McpWire, tool_name_of

__all__ = [
    "Entrypoint",
    "McpGateway",
    "McpTool",
    "McpWire",
    "build_mcp_server",
    "tool_name_of",
]
