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

The bus projection is shared: `Catalog` / `Operation` in [`catalog.py`](../../sincpro_framework/entrypoints/catalog.py). MCP and JSON-RPC only add a wire. REST/CLI should do the same — do not copy `collect_operations`.

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
| Feature raised | `-32603` Internal error |
| Business result on the response DTO | `result` (`isError` does not apply; this is not MCP) |

Binary DTOs (`bytes`) are skipped at catalog time, same as MCP.

Notifications (no `id`) run the Feature and return HTTP 204. Batch is JSON-RPC 2.0.

---

## Public API

| Symbol | Role |
|---|---|
| `RpcGateway({"qr": qr, ...})` | Composition root. Several instances, one process. |
| `.add(alias, framework)` | Fluent mount. |
| `.handle(payload, context=...)` | In-process JSON-RPC (tests, workers). No Starlette. |
| `.discover()` | OpenRPC 1.4 document. |
| `.app()` | Starlette: `POST /rpc`, `GET /openrpc.json`. |
| `.run(host=..., port=...)` | uvicorn. |
| `build_rpc_app(instances)` | Same as `RpcGateway(...).app()`. |

`rpc.discover` is also a JSON-RPC method (OpenRPC service discovery).

---

## Constraints

| Rule | Why |
|---|---|
| No JSON-RPC types on Feature | Hexagonal. |
| Do not share FastMCP's HTTP port | MCP methods would collide. |
| Alias has no dots | `instance.layer.Dto` split. |
| `params` is a JSON object | By-name, same shape as MCP arguments. |
| Starlette/uvicorn stay an extra | Core bus installs without an RPC stack. |
