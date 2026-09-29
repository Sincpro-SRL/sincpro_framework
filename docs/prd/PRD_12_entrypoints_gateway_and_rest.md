# PRD_12: One way to expose buses, on every wire — and REST with OpenAPI

- **Status**: built — `entrypoints.Gateway`, `entrypoints.internal`, `RestGateway`,
  `McpGateway`; documented in [entrypoints/integration](../entrypoints/integration.md) and
  [entrypoints/rest](../entrypoints/rest.md).
- **Depends on**: the catalog (`entrypoints.Catalog`), the shared executor, auth's transports
  (PRD_11 phase 2), `ddd.query.Query`.

## Problem

Each entrypoint exposed buses its own way: JSON-RPC and gRPC repeated the same `add(alias, bus,
include, exclude, wrap)`, MCP took one bus and chained `include()` calls, and a use case meant to
stay inside the process had to be excluded on every wire — one wire added later, and it was
public. There was no REST wire, and with it no OpenAPI document, the contract most client
generators, API gateways and third parties read.

## Decisions

### One base: `Gateway`

| Concern | Decision |
|---|---|
| **Automatic mode** | `Gateway([billing, sales])` publishes every use case of every bus, each under its own name made fit for the wire (`sincpro-billing` → `sincpro_billing` as a gRPC package). `{"alias": bus}` names it |
| **Narrowing** | `add(bus \| alias, bus, include=, exclude=, wrap=)` and `layers=` — the same on every wire |
| **Never published** | `@internal` on a handler or its Command — on no wire, now or added later; still run in the process |
| **The facade** | what a gateway builds is returned, never started: routes, an ASGI app, a gRPC server or handlers, a FastMCP server — the project adds middleware, interceptors, plugins |
| **Extending a wire** | a subclass answers `validate_alias`, `alias_for`, `added` — the three places a wire differs |
| **Compatibility** | the old forms stay: `RpcGateway({"a": bus})`, `add(alias, bus, ...)`, MCP's `Entrypoint(bus).include(...)`, `build_mcp_server(bus)` |

### REST: `RestGateway`

| Concern | Decision |
|---|---|
| **Method** | a Command is `POST`; a `Query` is `GET`, and `POST` to the same path for a criteria too long for a URL |
| **Path** | `{prefix}/{alias}/{kebab(DtoName)}` — never the layer, so moving a use case between Feature and ApplicationService keeps its URL; `prefix="/api/v1"` versions it from day one; `route(Dto, "GET /…/{field}")` for a path of the project's; two use cases on one method and path are refused |
| **Query string** | a plain field as itself; a `Criteria`, a list or an object as JSON — the form `@sincpro/criteria`'s `pack()` writes |
| **Names** | the DTO names are free (no prefix is stripped); `operationId` is the DTO's name, qualified by the alias when two buses answer it |
| **Failures** | 400 unreadable, 422 invalid or domain, 401 / 403 auth (with `WWW-Authenticate`), 409 `StaleAggregate` / `DuplicateAggregate`, 500 internal with nothing of the inside |
| **OpenAPI 3.1** | generated from the catalog like OpenRPC and the `.proto` files; `$defs` lifted to `components/schemas`; `securitySchemes` from the providers' new optional `security_scheme()`; `x-sincpro-requires` / `x-sincpro-when-denied` from the declarations; `/openapi.json` and a Swagger UI `/docs` |
| **Web library** | Starlette, like JSON-RPC, behind the `[rest]` extra — not FastAPI, which would validate each DTO a second time and answer 422 in another shape; FastAPI apps mount the routes all the same |

### One classification of failures

`entrypoints.errors.failure_kind` — invalid, unauthenticated, permission denied, conflict,
domain, internal — is shared by every wire, each answering with its own code. A domain refusal is
no longer `-32603 "Internal error"` on JSON-RPC (`-32010 "Domain error"`, `data` still its
message, as clients read it), and a stale or duplicate write is `-32009` / `ABORTED` /
`ALREADY_EXISTS` / 409, so a client knows to read again and retry.

### The catalog is computed once

`Catalog.get_scalar_use_cases` keeps its answer: a built bus's handlers do not change, and every
JSON-RPC request used to recompute every use case's schema. `include`, `exclude` and `wrap` let
it go.

### MCP: `McpGateway`

N buses as one server; a tool is named by its DTO, qualified by the alias when two buses answer
the same name.

## Proven

- `tests/entrypoint/test_entrypoint_rest.py`:
  - automatic mode, GET with criteria, POST fallback, path routes, the layer kept out of the URL;
  - each failure's status with nothing of the inside;
  - auth and its documentation;
  - every `$ref` of the document resolving, `operationId`s unique;
  - `internal` on no wire (REST, JSON-RPC, gRPC, MCP);
  - `include` / `exclude` / `layers`;
  - aliases fit per wire;
  - routes mounted into an app with its middleware.
- `docs/entrypoints/rest.md` runs as written.
- `test_core_without_extras`: the route table and the document build without Starlette, and serving asks for `[rest]`.

## Kept open, on purpose

- **JSON-RPC's body `context`** is the client's to write — documented, never read for identity.
- **Aliases from the bus's name** in the automatic mode — a public wire names them explicitly.
- **Adding a bus to a gateway builds it** — gateways are made last in the composition; done the
  other way, `BusAlreadyBuilt` says so.
- **MCP tool annotations** (`readOnlyHint` for a `Query`, `destructiveHint`) — hints for an agent,
  proposed, not built.

## Next

1. **Streaming answers** — a `Query` answering a `DataFrame` as Arrow IPC
   (`application/vnd.apache.arrow.stream`), which OpenAPI 3.2 describes as a streaming media type.
2. **Realtime** — a resumable SSE feed of `DomainEvent`s filtered by identity (see the protocol
   research: SSE with `Last-Event-ID`, Centrifugo or NATS-over-WebSocket at scale).
3. **Connect** — the same catalog as the Connect protocol, for typed clients on web and React
   Native, hand-rolled on ASGI (unary + server streaming) until `connect-py` is 1.0.
4. **HTTP caching** — `ETag` / `Cache-Control` on `GET` answers of a `QueryCaching`-cached Query.
