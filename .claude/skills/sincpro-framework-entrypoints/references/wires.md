# The wires

Each gateway publishes the same catalog with its own names. Full depth: `docs/entrypoints/{rest,rpc,grpc,mcp,fastapi}.md`.

## REST — `RestGateway` (Starlette, frozen) and `FastApiGateway` (the host now)

| DTO | Route |
|---|---|
| a Command | `POST /{alias}/{kebab-name}`, body is the DTO |
| a `Query` | `GET /{alias}/{kebab-name}?field=…&criteria=<json>` (or `POST` when the criteria is too long) |
| a project's path | `rest.route(Dto, "GET /billing/invoices/{invoice_id}")` |

The path never carries the layer. `openapi()` gives OpenAPI 3.1 (`operationId` is the DTO's name).
`RestGateway` is frozen; **`FastApiGateway` is the host for new REST** — declared resources, RFC 9457
problem details, `Idempotency-Key`, one OpenAPI document for generated and hand-written routes:

```python
from sincpro_framework.entrypoints.fastapi import FastApiGateway
api = FastApiGateway([billing], title="Billing API")
api.group(billing, version="v1")        # /v1/billing/...
app = api.app()                         # install_problem_handlers for free
```

`FastApiGateway` profiles: `resource` (declared `@rest...`) or `profile="rpc"` (catalog, RPC over
HTTP). It also serves `/metrics` when the process records to Prometheus.

- The `@rest...` decorator goes on the existing handler in `services/`; `entrypoints/http/app.py`
  only builds the gateway. Never register a Feature or a bus in `entrypoints/` to get a route.
- A body that is not the Command (a file as base64) → a hand-written route that translates and
  calls `Depends(bus_call(bus))`, included in `api.app()`, checked with `api.verify(app)`.
  `docs/entrypoints/fastapi.md` ("Where each piece lives", "A DTO that cannot travel as JSON").

## JSON-RPC 2.0 — `RpcGateway`

One `POST /rpc`, discovery OpenRPC 1.4 (`GET /openrpc.json`, `rpc.discover`). Method names are
`{namespace}.{operation}` (`billing.issue_invoice`), declared by default (`@rpc()`), catalog with
`Exposure.CATALOG`. `params` are the DTO fields, by name. `context` is a sibling of `params` (not a
field): `with framework.context({...})`, plus `trace_id`/`span_id`/`carrier` → `with_trace(...)`.

- **No layer in any name**: promoting a Feature to an ApplicationService renames nothing.
- Errors carry `data.kind`, `data.reason`, `data.retryable`.
- `.routes()` for a host's own app; `.handle(payload, context=, credentials=)` in-process.
- Limits: `max_batch_size=50`, `max_body_bytes=1 MiB`.

## gRPC — `GrpcGateway`

AIP names: package `billing.v1`, service `BillingService`, method `IssueInvoice`. **Unary only**,
`google.protobuf.Struct` in and out (field names verbatim). Server reflection (v1 + v1alpha) and
`sincpro.Introspection/Describe`; no `protoc`, no generated stubs. `.write_proto_files(dir)` for a
Go/TS client at build time. `.server()` / `.handlers()` / `.mount()` for a host's own server;
`GrpcClient(target)` is a dict-in/dict-out Python caller.

- **The response shape** is the declared `execute` return annotation (or
  `Feature[Command, Response, Ctx]`); an unannotated `execute` still runs, only the promise is
  missing.
- Health (`grpc.health.v1`) and graceful shutdown (SIGTERM → `NOT_SERVING` → drain) are on by
  default. Backpressure: `maximum_concurrent_rpcs` (default 2 × workers), past it
  `RESOURCE_EXHAUSTED`.
- Deadlines: `grpc-timeout` enters as `context["deadline"]` (epoch seconds); a call past its
  deadline answers `DEADLINE_EXCEEDED` and never runs.
- `bus_call(context, bus, dto, to_response=None)` runs the generated path for a hand-written
  servicer; `framework_interceptors()` for a server of yours (never auth).

## MCP — `McpGateway`

Tools named by the DTO without `Command`/`Query`, snake_case. Docstrings are the LLM context.
`McpGateway([billing]).server()` returns a real FastMCP instance (`[mcp]` extra); `.run()` is stdio,
`run(transport="http", port=…)` is Streamable HTTP MCP.

- Instruction resolution: the class's own docstring, else `execute`'s, else the DTO's, else the DTO
  name. Write it as an instruction — what the tool does, when to use it, what it does not do.
- Hints (`read_only`, `destructive`, `idempotent`, `open_world`) are derived from the Command's kind
  and the bindings; **hints are never authorization**.
- `build_mcp_server(bus)` / `Entrypoint(bus)` stay the catalog of one bus, named by DTO class.

## Binary DTOs: what each wire carries

Whether a `bytes` field can travel is the wire's, declared on its port (`Wire.carries_bytes`).
The built-in gateways carry JSON today — REST (`FastApiGateway`), JSON-RPC, MCP, the queue body,
and the gRPC gateway, whose payload is `google.protobuf.Struct` (unary, no protobuf `bytes`).
That is the gateways' current design, not a limit of HTTP or gRPC. On those wires a DTO with a
`bytes` field bound with `@rest.post`, `@mcp()`, `bind`… is refused (`verify()` names it, the
build raises `ExposureRefused`); unbound, it is skipped with one warning, `Skipping non-JSON
Feature/ApplicationService [Command…]`. It stays callable in-process. To publish it on a JSON
wire, write the route by hand and call the bus (REST: `bus_call`). A project's own `Wire` with
`carries_bytes = True` publishes it as it is.

Between Sincpro Python services, `remote_execution` (a context map, `bus.serve(...)`) is the gRPC
that carries `bytes`, `Decimal`, `datetime`… as they are: one `stream_stream` call, the DTO in
1 MiB chunks each way. See references/remote-execution.md.

## Picking one

| Need | Wire |
|---|---|
| Browser/third parties, generated clients | REST (`FastApiGateway`) |
| A compact method call from another service, OpenRPC discovery | JSON-RPC |
| Typed cross-language clients, reflection, streaming-free RPC | gRPC |
| An agent / LLM tool catalog | MCP |
| Consuming Commands/events from a broker | `QueueGateway` (see queue.md) |
