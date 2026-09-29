# Entrypoints: the bus catalog, exposed

A `UseFramework` already knows every DTO it answers and what each one means. The entrypoints
publish that catalog over a transport without the domain learning about the transport.

| Page | What it answers |
|---|---|
| [Integration](integration.md) | what every wire shares: N buses automatically, `include` / `exclude` / `layers` / `internal`, the app or server handed back for the project's middleware |
| [REST](rest.md) | `RestGateway`: Commands on POST, `Query`s on GET, an OpenAPI 3.1 document from the catalog, `/docs` |
| [JSON-RPC 2.0](rpc.md) | `entrypoint_rpc`: one or more instances as JSON-RPC methods, discovery through OpenRPC 1.4 |
| [gRPC](grpc.md) | `entrypoint_grpc`: the same catalog as unary gRPC services over `google.protobuf.Struct`, reflection for discovery |
| [Bounded contexts across services](bounded-contexts-across-services.md) | the context map: a bounded context hosted by another service that runs the same code, over gRPC or HTTP — and `serve(...)`, its Open Host Service; `sincpro_framework.remote_execution` |
| [MCP](mcp.md) | `entrypoint_mcp`: Features and ApplicationServices as MCP tools; docstrings are the LLM's context |
| [MCP, a real use case](mcp-use-case.md) | The pattern evaluated against the SIAT SOAP SDK Sincpro ships |

They are extras: `[rest]`, `[rpc]`, `[grpc]` and `[mcp]`. A service that only runs a bus installs none of them.
