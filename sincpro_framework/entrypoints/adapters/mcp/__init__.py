"""The MCP wire: use cases as tools of a FastMCP 3 server, every call through the bus — what
`McpGateway` and the MCP `Entrypoint` build on.

    McpWire().build(surface)        ->  [McpTool(name="issue_invoice", ...), ...]
    tool_name_of(CommandIssueInvoice)  ->  "issue_invoice"

Importing it needs no `[mcp]` extra: FastMCP is imported only when a server is built.
"""

from sincpro_framework.entrypoints.adapters.mcp.wire import McpTool, McpWire, tool_name_of

__all__ = [
    "McpTool",
    "McpWire",
    "tool_name_of",
]
