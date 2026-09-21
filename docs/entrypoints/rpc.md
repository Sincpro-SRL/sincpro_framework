# `entrypoint_rpc` — bus catalog as JSON-RPC 2.0 methods

**`entrypoint_rpc`** publishes one or more `UseFramework` instances as JSON-RPC 2.0 methods. Discovery is **OpenRPC 1.4**. This is not REST, not MCP, not FastAPI-jsonrpc's fake OpenAPI routes.

The domain does not know JSON-RPC exists. `params` are the DTO fields. `framework.context` / `with_trace` are optional extras on the request, never fields of the Command.

```
entrypoint_rpc                    Application                 Domain
────────────────                  ────────────                ──────
HTTP POST /rpc                    UseFramework × N            Feature
GET /openrpc.json                 FeatureBus                  ApplicationService
rpc.discover                      registries                  DataTransferObject
Python remains: framework(dto)                                ValueObject
```

Package: `sincpro_framework.entrypoints.rpc` (`RpcGateway`, `build_rpc_app`). Feature name: **`entrypoint_rpc`**.

The bus projection is shared: `sincpro_framework.introspection` describes what exists (`FeatureOrAppServiceMetadata`/`DtoMetadata`), `Catalog` / `PackedFeatureOrAppService` in [`catalog.py`](../../sincpro_framework/entrypoints/catalog.py) packages it for JSON. MCP and JSON-RPC only add a wire. REST/CLI should do the same — reuse `Catalog`, do not reimplement it.

MCP stays `entrypoint_mcp`. Do not share a port with FastMCP — MCP already uses JSON-RPC methods `tools/list` / `tools/call`.

---

## Why Starlette + OpenRPC, not FastAPI-jsonrpc

JSON-RPC 2.0 is the current protocol. OpenRPC 1.4 is the current discovery document (`rpc.discover`, `GET /openrpc.json`).

The catalog is **dynamic**: `{instance}.{layer}.{DtoName}` from the bus. FastAPI-jsonrpc wants `@method()` at import time and advertises each method as a REST POST in Swagger. That would sell this extra as OpenAPI. Starlette is the ASGI layer FastMCP HTTP already sits on; we speak JSON-RPC on one path.

`jsonrpc-py` (ASGI-native, OpenRPC) requires Python 3.14. This project is 3.12.

---

## Method names

```text
{alias}.{layer}.{DtoName}
```

| Layer | Bus registry |
|---|---|
| `features` | Feature |
| `app_services` | ApplicationService |

Alias is the composition-root key (`qr`, `cybersource`, `bank_account`), not `_logger_name`. DTO names collide across bounded contexts; the prefix is required.

Default: both layers. Payments today is almost all Features. SIAT uses both. Filter with `layers=("app_services",)` when you want a smaller surface.

```python
from sincpro_framework.entrypoints.rpc import RpcGateway
from sincpro_payments_sdk.apps.qr import qr
from sincpro_payments_sdk.apps.cybersource import cybersource
from sincpro_payments_sdk.apps.bank_account import bank_account

RpcGateway({
    "qr": qr,
    "cybersource": cybersource,
    "bank_account": bank_account,
}).run()
```

```bash
pip install sincpro-framework[rpc]
```

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "qr.features.CommandCreateQREconomico",
  "params": {
    "transaction_id": "t-1",
    "account_credit": "100000123",
    "currency": "BOB",
    "amount": 50.0,
    "description": "invoice"
  }
}
```

`POST http://127.0.0.1:8080/rpc` — still JSON-RPC, not `POST /qr/features/...`.

---

## Context and tracing

JSON-RPC 2.0 allows extra members on the request. **`context`** is a sibling of `params`, so it cannot collide with a DTO field.

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "qr.features.CommandCheckQRStatusEconomico",
  "params": { "transaction_id": "t-1" },
  "context": {
    "correlation_id": "req-9",
    "user.id": "operator",
    "trace_id": "abc",
    "span_id": "def"
  }
}
```

| You send | Framework |
|---|---|
| `context` keys | `with framework.context({...})` — Feature reads `self.context` |
| `trace_id` / `span_id` / `carrier` inside `context` | also `with framework.with_trace(...)` |
| HTTP `X-Correlation-Id` | `correlation_id` if the body omitted it |
| HTTP `traceparent` | `carrier.traceparent` for OTel parent adoption |

Body `context` wins over headers. Middleware, error handlers, and bus tracing are unchanged.

---

## Errors

| Level | JSON-RPC |
|---|---|
| Parse / invalid request | `-32700` / `-32600` |
| Unknown method | `-32601` |
| DTO / Pydantic | `-32602` Invalid params |
| Feature raised a `DomainError` | `-32603` Internal error, with its message in `data` — it was written for the caller |
| Feature raised anything else | `-32603` Internal error, **and nothing else**. The exception's own text carries connection strings, statements and paths; it goes to the log, not over the wire |
| Business result on the response DTO | `result` (`isError` does not apply; this is not MCP) |

The OpenRPC `result.schema` is the **declared** response's JSON Schema — `execute`'s return
annotation, or `Feature[Command, Response, Ctx]`'s second parameter. A DTO, a dataclass, an
`Entity`, a `list[...]` all publish; an unannotated `execute` publishes `{"type": "object"}`.
See [grpc.md](grpc.md#what-comes-back-the-response-shape), which documents the shared rule.

Binary DTOs (`bytes`) are skipped at catalog time, same as MCP.

Notifications (no `id`) run the Feature and return HTTP 204. Batch is JSON-RPC 2.0.

---

## Health — on by default

`.routes()`/`.app()` serve `GET /healthz` unless `health_path=None`: `200 {"status": "ok"}`
when every registered bus is still built, `503 {"status": "unhealthy"}` otherwise — the same
question `entrypoint_grpc`'s default health check asks, over plain HTTP for a mesh or load
balancer that doesn't speak gRPC.

```python
RpcGateway({"qr": qr}).app()                       # GET /healthz included
RpcGateway({"qr": qr}).app(health_path=None)        # host wires its own instead
RpcGateway({"qr": qr}).routes(health_path="/live")  # custom path, for composing
```

`RpcGateway.is_healthy()` is the check itself, for a host that wants the boolean without the
route (its own health endpoint, a startup probe before serving traffic).

---

## Composability: routes as data, not an opinion

`entrypoint_rpc` never decides CORS, auth, health checks or process lifecycle — it hands the
host the same thing FastAPI hands you as `app.routes`: data. Two levels:

- **`.routes()`** — a plain `list[starlette.routing.Route]`, no `Starlette` around it. Mount it
  into an app the host already owns, alongside its own health check, its own CORS
  `Middleware`, several gateways under one process, whatever the host's own framework needs.
- **`.app(middleware=, routes=, lifespan=)`** — the convenience path, for a process that serves
  only this gateway, still composable: extra `Middleware(...)` instances, extra routes, a
  Starlette `lifespan` for startup/shutdown (opening a pool, warming a cache).

```python
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Route

gateway = RpcGateway({"qr": qr, "cybersource": cybersource})

app = Starlette(
    routes=[Route("/healthz", healthz), *gateway.routes()],
    middleware=[Middleware(CORSMiddleware, allow_origins=["https://app.example.com"])],
)
```

**Per-bus policy at the HTTP layer:** CORS is a property of the *origin/port* a browser talks
to — a single JSON-RPC `POST /rpc` cannot carry two different CORS answers depending on which
`method` the body names, a preflight `OPTIONS` never reaches the body. If two sets of buses need
different browser-facing policies, give them different `RpcGateway`s — each with its own
`middleware=` — mounted at different paths or served on different ports/subdomains, not one
gateway trying to branch on `method` after the fact. Auth *can* branch per method (a wrapper via
`wrap(dto, wrapper)`, or middleware that reads the parsed body), because unlike CORS it is
enforced after the request already arrived.

---

## Public API

| Symbol | Role |
|---|---|
| `RpcGateway({"qr": qr, ...})` | Composition root. Several instances, one process. |
| `.add(alias, framework)` | Fluent mount. |
| `.handle(payload, context=...)` | In-process JSON-RPC (tests, workers). No Starlette. |
| `.discover()` | OpenRPC 1.4 document. |
| `.routes(rpc_path=, discover_path=, health_path=)` | This gateway's `Route`s (health included), for a host's own app. |
| `.app(middleware=, routes=, lifespan=, health_path=, **kwargs)` | Starlette: `POST /rpc`, `GET /openrpc.json`, `GET /healthz`, composable. |
| `.health_route(path=)` | Just the health `Route`, for a host composing `.routes()` piecemeal. |
| `.is_healthy()` | The health check itself, no route — every registered bus still built. |
| `.run(host=..., port=...)` | uvicorn. |
| `build_rpc_app(instances)` | Same as `RpcGateway(...).app()`. |

`rpc.discover` is also a JSON-RPC method (OpenRPC service discovery).

---

## Module map

```
sincpro_framework/
├── introspection/         # shared: bus → FeatureOrAppServiceMetadata/DtoMetadata (name, type, own-docstring description)
└── entrypoints/
    ├── scalar_executor.py # shared: Scalar (dict) in/out execution against a UseFramework
    ├── catalog.py         # shared: FeatureOrAppServiceMetadata → PackedFeatureOrAppService (JSON schema, binary check)
    └── rpc/               # entrypoint_rpc
        ├── __init__.py    # re-exports RpcGateway, build_rpc_app
        ├── jrpc.py        # JSON-RPC-specific wire: dispatch, errors, OpenRPC document
        └── entrypoint.py  # orchestrates catalog.py + jrpc.py — the RpcGateway facade
```

Same split as `entrypoint_mcp`: `entrypoint.py` orchestrates, `jrpc.py` holds everything specific to the JSON-RPC wire. A future protocol under `entrypoints/` follows the same two-file recipe. `jrpc.py`'s `execute()` (context/tracing around a call) comes from `scalar_executor.py`, not from `catalog.py` — it is orthogonal to packaging metadata into a wire DTO, and MCP does not need it (FastMCP has no per-request context sibling field the way JSON-RPC does).

---

## Constraints

| Rule | Why |
|---|---|
| No JSON-RPC types on Feature | Hexagonal. |
| Do not share FastMCP's HTTP port | MCP methods would collide. |
| Alias has no dots | `instance.layer.Dto` split. |
| `params` is a JSON object | By-name, same shape as MCP arguments. |
| Starlette/uvicorn stay an extra | Core bus installs without an RPC stack. |
