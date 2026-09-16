# Entrypoints: the bus catalog, exposed

A `UseFramework` already knows every DTO it answers and what each one means. The entrypoints
publish that catalog over a transport without the domain learning about the transport.

| Page | What it answers |
|---|---|
| [JSON-RPC 2.0](rpc.md) | `entrypoint_rpc`: one or more instances as JSON-RPC methods, discovery through OpenRPC 1.4 |
| [MCP](mcp.md) | `entrypoint_mcp`: Features and ApplicationServices as MCP tools; docstrings are the LLM's context |
| [MCP, a real use case](mcp-use-case.md) | The pattern evaluated against the SIAT SOAP SDK Sincpro ships |

Both are extras: `[rpc]` and `[mcp]`. A service that only runs a bus installs neither.
