# Integrating buses with an entrypoint — one way, on every wire

Every entrypoint — JSON-RPC, gRPC, MCP, REST — extends one base, `entrypoints.Gateway`, so
exposing buses is learned once and reads the same on every wire.

## Automatic: give it the buses

```python
RestGateway([billing, sales])
RpcGateway([billing, sales])
GrpcGateway([billing, sales])
McpGateway([billing, sales]).server()
```

Every Feature and ApplicationService of every bus is published, each bus under its own name made
fit for the wire: `sincpro-billing` is the REST and JSON-RPC alias as it is, and the gRPC package
`sincpro_billing` — a proto package allows no hyphen. `{"billing": billing}` names the alias
instead.

## Narrowed: say what not to publish

| Want | How | Scope |
|---|---|---|
| A use case on **no** wire, ever — run only in the process | `@internal` on the handler or its Command (`from sincpro_framework.entrypoints import internal`) | every gateway, added now or later |
| Only some use cases of a bus on one wire | `gateway.add(bus, include=[CommandA, "CommandB"])` | that gateway |
| All but some | `gateway.add(bus, exclude=[CommandReconcile])` | that gateway |
| One layer only | `Gateway(..., layers=("app_services",))` | that gateway |
| An alias of the caller's | `gateway.add("ventas", sales)` | that gateway |
| A run decorated — audit, extra logging | `gateway.add(bus, wrap={CommandIssue: audit})` | that gateway |
| REST: a path of the project's | `rest.route(QueryInvoice, "GET /billing/invoices/{invoice_id}")` | that gateway |

`include`, `exclude` and `wrap` take the Command class or its name; a name no use case answers is
warned about, since in `exclude` it would leave exposed what it meant to hide.

## The facade: what it builds is handed back

A gateway never starts anything by itself. What it builds is returned, so the project adds its
middleware, interceptors and plugins:

| Wire | Returned | Where the project extends it |
|---|---|---|
| REST | `rest.routes()` · `rest.app(middleware=, routes=, lifespan=)` · `rest.openapi()` | Starlette middleware (CORS, `IdentityMiddleware`, rate limits), mounting into FastAPI, extra routes |
| JSON-RPC | `rpc.routes()` · `rpc.app(...)` · `rpc.discover()` | the same |
| gRPC | `grpc.handlers()` · `grpc.server(interceptors=, options=)` · `grpc.proto_files()` | `grpc.ServerInterceptor`s, its own `grpc.Server`, TLS credentials |
| MCP | `mcp.server(auth=, base_url=)` — a FastMCP instance | FastMCP middleware, prompts, resources |

`.run(...)` on each is the one-liner for a service that serves only that wire.

## Practices

- **A public wire gets explicit aliases.** The automatic mode publishes each bus under its own
  name — the name its logs and error tracker use too. For a wire third parties or agents build
  on, name the alias yourself (`add("billing", bus)`): renaming the bus then never moves a URL, a
  gRPC package or a tool.
- **Gateways are made last.** Adding a bus to a gateway builds it, and a built bus takes nothing
  more — so `auth.on(bus)`, the interceptors and the handlers are registered first, the gateways
  at the end of the composition. Done the other way, `BusAlreadyBuilt` says what came too late.
- **What the client sends is the client's.** JSON-RPC opens the `context` a request carries, and
  every wire opens the DTO's fields as they came; the framework validates the DTO and never takes
  identity from either. A use case that decides on a context value validates it.

## Failures — one kind, each wire's code

| Kind | Raised | REST | JSON-RPC | gRPC | Told to the caller |
|---|---|---|---|---|---|
| `invalid` | the DTO did not validate | 422 (400 if unreadable) | `-32602` | `INVALID_ARGUMENT` | the validation errors |
| `unauthenticated` | `Unauthenticated` | 401 + `WWW-Authenticate` | `-32001` (a lone call: HTTP 401) | `UNAUTHENTICATED` | the reason, `step_up` |
| `permission_denied` | `PermissionDenied` | 403 | `-32003` | `PERMISSION_DENIED` | the reason, the requirement |
| `conflict` | `StaleAggregate`, `DuplicateAggregate` | 409 | `-32009` | `ABORTED` / `ALREADY_EXISTS` | the message — read again and retry |
| `domain` | any other `DomainError` | 422 | `-32010` | `FAILED_PRECONDITION` | the message |
| `internal` | anything else | 500 | `-32603` | `INTERNAL` | nothing — it stays in the log |

`entrypoints.errors.failure_kind(error)` is the one classification; a wire only chooses its code.

## What every wire does the same

- **Auth**: a bus guarded by an `AccessControl` is authenticated on every wire, and refused the
  way each protocol's clients understand — see [auth](../auth/README.md#entrypoints-authenticate-by-themselves).
- **Validation**: the DTO validates the payload; a refusal is the wire's "invalid" answer.
- **Disclosure**: a `DomainError`'s message reaches the caller; anything else stays in the log.
- **Health**: `gateway.is_healthy()` — every bus still built — behind each wire's health check.
- **Optional**: a wire's library (`starlette`, `grpc`, `fastmcp`) is read only when that wire is
  served; building a gateway, its route table or its document needs none of them.
