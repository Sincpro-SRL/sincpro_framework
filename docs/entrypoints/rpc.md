# `entrypoint_rpc` — declared use cases as JSON-RPC 2.0 methods

**`entrypoint_rpc`** publishes the use cases of one or more `UseFramework` instances that are
**declared** for JSON-RPC (`@rpc()`, PRD_14) as JSON-RPC 2.0 methods named
`{namespace}.{operation}` — `billing.issue_invoice` (PRD_15 §4). Discovery is **OpenRPC 1.4**. This is not REST, not MCP, not FastAPI-jsonrpc's fake OpenAPI routes.

The domain does not know JSON-RPC exists. `params` are the DTO fields. `framework.context` / `with_trace` are optional extras on the request, never fields of the Command.

```
entrypoint_rpc                    Application                 Domain
────────────────                  ────────────                ──────
HTTP POST /rpc                    UseFramework × N            Feature
GET /openrpc.json                 FeatureBus                  ApplicationService
rpc.discover                      registries                  DataTransferObject
Python remains: framework(dto)                                ValueObject
```

Package: `sincpro_framework.entrypoints.rpc` (`RpcGateway`, `build_rpc_app`, `JsonRpcWire`,
`RpcSurface`, `dispatch`, `http_status`, `operation_name`). Feature name: **`entrypoint_rpc`**.

The bus projection is shared: `sincpro_framework.introspection` describes what exists (`FeatureOrAppServiceMetadata`/`DtoMetadata`), `Catalog` / `PackedFeatureOrAppService` in [`catalog.py`](../../sincpro_framework/entrypoints/catalog.py) packages it for JSON. MCP and JSON-RPC only add a wire. REST/CLI should do the same — reuse `Catalog`, do not reimplement it.

MCP stays `entrypoint_mcp`. Do not share a port with FastMCP — MCP already uses JSON-RPC methods `tools/list` / `tools/call`.

---

## Why Starlette + OpenRPC, not FastAPI-jsonrpc

JSON-RPC 2.0 is the current protocol. OpenRPC 1.4 is the current discovery document (`rpc.discover`, `GET /openrpc.json`).

The surface is **derived**: `{namespace}.{operation}` from the bus's group and the DTO. FastAPI-jsonrpc wants `@method()` at import time and advertises each method as a REST POST in Swagger. That would sell this extra as OpenAPI. Starlette is the ASGI layer FastMCP HTTP already sits on; we speak JSON-RPC on one path.

`jsonrpc-py` (ASGI-native, OpenRPC) requires Python 3.14. This project is 3.12.

---

## Which use cases — declared exposure

The gateway resolves a **surface** (PRD_14): in `Exposure.DECLARED`, **the default**, only the use
cases bound for JSON-RPC are methods; everything else is not reachable, nor in the document.

```python
from sincpro_framework.entrypoints.exposure import rpc

@billing.feature(CommandIssueInvoice)
@auth.requires(BillingPermission.ISSUE)
@rpc()                                   # billing.issue_invoice
class IssueInvoice(Feature): ...

@billing.feature(QueryInvoice)
@auth.public
@rpc("billing.invoices.get")             # a whole name of its own
class GetInvoice(Feature): ...

RpcGateway({"billing": billing}).run()
```

| Mode | Methods |
|---|---|
| `RpcGateway(buses)` — `Exposure.DECLARED` | the use cases with `@rpc()`, or `bind`-ed by the composition |
| `RpcGateway(buses, exposure=Exposure.CATALOG)` | every use case of every bus (JSON-safe, in `layers`), under the **same names**; each one logged as it is published |

The composition's word, the same on every wire: `add(alias, bus, include=, exclude=)`,
`group(bus, namespace=, version=)`, `override(Command, name=...)` (shapes a binding, never
publishes), `bind(Command, RpcBinding(...))` (publishes), `@internal` (never on any wire).
`surface()`, `manifest()` and `verify()` are the core's (PRD_14): the manifest's `name` is the
JSON-RPC method name.

**Who may call it.** A published method on a bus guarded by `AccessControl` must declare
`@auth.requires(...)` or `@auth.public`; a bus with no `AccessControl` is refused unless the
gateway says so on purpose: `RpcGateway(buses, unguarded=True)`. Both are build errors, listed
together, before anything is served.

---

## Method names

```text
{namespace}.{operation}          billing.issue_invoice
{namespace}.{version}.{operation}    billing.v2.issue_invoice
```

| Part | Rule |
|---|---|
| namespace | the context's group: `group(bus, namespace="billing")`, the **alias** by default, made snake_case (`sincpro-billing` → `sincpro_billing`); `group(bus, version="v2")` appends the breaking version (as does `namespace="billing.v2"`) |
| operation | the DTO's name without `Command` / `Query`, in snake_case: `CommandIssueInvoice` → `issue_invoice`, `QueryInvoice` → `invoice`, `CommandCreateQREconomico` → `create_qr_economico`, `ValidateCard` → `validate_card` |
| a declared name | `@rpc("billing.invoices.issue")` or `override(Command, name=...)` is the **whole** name — the group does not prefix it |

**No layer, in any mode.** `{alias}.{layer}.{Dto}` (`billing.features.CommandIssueInvoice`) is
**removed**, with no transition: promoting a Feature to an ApplicationService is an internal
change and renames nothing a client calls (tested). `layers=` still narrows what CATALOG mode
publishes; it never appears in a name, a tag or the document.

The build refuses (`ExposureRefused`, every reason at once):

- a name that is not dotted snake_case with at least two segments (`issue`, `Billing.Issue`,
  `billing.issue-invoice`);
- a name under `rpc.` — only `rpc.discover` uses it — whether declared or derived from a group
  namespace `rpc`; a bus **aliased** `rpc` is refused when it is added;
- two operations answering one name after derivation (`CommandInvoice` and `QueryInvoice` are both
  `billing.invoice`; two buses grouped under one namespace) — name one with `@rpc(name=...)`.

A different derivation is a port of the project's: subclass `JsonRpcWire` (`operation_of`,
`namespace_of`) and hand it to `RpcGateway(port=...)`. Any other `Wire` is refused.

```python
RpcGateway({
    "qr": qr,
    "cybersource": cybersource,
}).run()
```

```bash
pip install sincpro-framework[rpc]
```

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "qr.create_qr_economico",
  "params": {
    "transaction_id": "t-1",
    "account_credit": "100000123",
    "currency": "BOB",
    "amount": 50.0,
    "description": "invoice"
  }
}
```

`POST http://127.0.0.1:8080/rpc` — still JSON-RPC, not `POST /qr/...`.

---

## Context and tracing

**`context` is a framework extension, not JSON-RPC 2.0.** The specification defines the
request members `jsonrpc`, `method`, `params` and `id`, and says nothing of any other. This
gateway reads one more, **`context`**, a sibling of `params` so it cannot collide with a DTO
field; the OpenRPC document publishes it as the `x-sincpro-context` extension. A client written
for any other JSON-RPC server should not send it, and another server will ignore it (or refuse
the request). A `context` that is not an object is an Invalid Request (`-32600`).

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "qr.check_qr_status_economico",
  "params": { "transaction_id": "t-1" },
  "context": {
    "correlation_id": "req-9",
    "user_id": "operator",
    "trace_id": "abc",
    "span_id": "def"
  }
}
```

| You send | Framework |
|---|---|
| `context` keys | `with framework.context({...})` — Feature reads `self.context` |
| `trace_id` / `span_id` / `carrier` inside `context` | also `with framework.with_trace(...)` |
| HTTP `X-Correlation-Id`, `X-Causation-Id`, `X-Execution-Id` | the execution's identity if the body omitted it — [context-manager.md](../core/context-manager.md#execution-identity) |
| HTTP `traceparent` | `carrier.traceparent` for OTel parent adoption |

Body `context` wins over headers. Interceptors, error handlers, and bus tracing are unchanged.

**The client writes it.** The framework opens every key it sends — a freedom the client
programs with, not a check the framework makes. Who is calling is never read from it (see
[auth](../auth/README.md)); anything else a use case takes from `self.context` to decide
something, the use case validates, as it would any input.

---

## Errors

Every error answers the JSON-RPC `code` and `message`, and a **`data` object that always
carries three members** — what a client switches on, rather than the number (the numbers above
`-32000` mean other things in other APIs):

| `data` member | What it is |
|---|---|
| `kind` | The failure's kind, the same on every wire (`transport.failures`): `invalid`, `unauthenticated`, `permission_denied`, `not_found`, `conflict`, `in_progress`, `key_reused`, `domain`, `exhausted`, `unavailable`, `internal` |
| `reason` | UPPER_SNAKE of the error's class — `CONTRACT_VIOLATION`, `STALE_AGGREGATE`, `VALIDATION_ERROR` — or of the protocol refusal (`BATCH_TOO_LARGE`, `PARAMS_BY_POSITION`). An internal failure is always `INTERNAL_ERROR`: its class is the inside of the process |
| `retryable` | Whether the same request may succeed if sent again |

Whatever else a client read before is still there, under a named member: `message` (a
`DomainError`'s text, an auth refusal's reason, a protocol refusal's detail), `errors` (the
Pydantic validation errors), `method` (the unknown method), `requirement` / `step_up` (an auth
refusal), `limit` / `size` (a limit). **Breaking:** `data` used to be the bare message string for
a domain refusal and the bare method name for `-32601`; it is `data.message` / `data.method`
now. And an auth refusal's `data.reason` was its text; the text is `data.message`, `reason` is
`UNAUTHENTICATED` / `PERMISSION_DENIED`.

| Code | `message` | `kind` | `retryable` | Raised by |
|---|---|---|---|---|
| `-32700` | Parse error | invalid | no | the body is not JSON (or not UTF-8) |
| `-32600` | Invalid Request | invalid | no | not a Request object, a wrong `id` type, `jsonrpc` ≠ `"2.0"`, an empty batch, a batch over the limit, a `context` that is not an object |
| `-32601` | Method not found | not_found | no | no such method |
| `-32602` | Invalid params | invalid | no | the DTO did not validate (`data.errors`), or params by position |
| `-32001` | Unauthenticated | unauthenticated | no | `Unauthenticated` — a lone request is also HTTP 401 + `WWW-Authenticate` |
| `-32003` | Permission denied | permission_denied | no | `PermissionDenied` |
| `-32004` | Not found | not_found | no | an error declaring `failure_kind = FailureKind.NOT_FOUND` |
| `-32009` | Conflict | conflict | stale: yes, duplicate: no | `StaleAggregate` (a fresh run reads again), `DuplicateAggregate` (sending it again collides again) |
| `-32029` | Try again later | in_progress / exhausted | yes | `caching.AlreadyInProgress` — the same idempotency key is running; an error declaring `FailureKind.EXHAUSTED` |
| `-32022` | Idempotency key reused | key_reused | no | `caching.KeyReused` — a new request needs a new key |
| `-32010` | Domain error | domain | no | any other `DomainError`, its text in `data.message` |
| `-32000` | Unavailable | unavailable | yes | an error declaring `FailureKind.UNAVAILABLE` |
| `-32603` | Internal error | internal | yes | anything else — **nothing of it is told**: the exception's text carries connection strings, statements and paths; it goes to the log |

The catalogue is published **once**, in the OpenRPC document's `components.errors` (and the
shape of `data` as `components.schemas.ErrorData`); every method's `errors` references the
codes a method can answer with `$ref` — the three protocol codes (`-32700`, `-32600`, `-32601`)
are answered before any method is known, so no method lists them.

The OpenRPC `result.schema` is the **declared** response's JSON Schema — `execute`'s return
annotation, or `Feature[Command, Response, Ctx]`'s second parameter. A DTO, a dataclass, an
`Entity`, a `list[...]` all publish; an unannotated `execute` publishes `{"type": "object"}`.
See [grpc.md](grpc.md#what-comes-back-the-response-shape), which documents the shared rule.
Each method object carries, besides its params and result:

| Member | What |
|---|---|
| `tags` | one tag, the **context** (the bus's alias) — the document is tagged per context |
| `x-sincpro-context`, `x-sincpro-dto` | the context and the DTO the method runs |
| `x-sincpro-requires` | what the use case declared: `public`, `authenticated`, its requirements (`billing.invoice.issue`), or `unguarded` |
| `x-idempotent` | `true` for a `Query` (it reads) or a use case under `@idempotency.once` — a retry is safe |
| `deprecated`, `x-sunset` | from the binding's `Deprecation` |

A nested DTO's definitions are published in `components.schemas` and referenced from there, so
every `$ref` of the document resolves. The document validates against the OpenRPC 1.4
meta-schema (`tests/entrypoint/test_jsonrpc_errors.py`).

JSON-RPC params are JSON (`carries_bytes = False` on its wire): binary DTOs (`bytes`) are skipped, same as MCP. Declaring `@rpc()` on one is refused: `verify()` names it and the build raises `ExposureRefused`.

---

## Batches, notifications and `id`

| Request | Answer |
|---|---|
| A notification (a valid request without `id`) | runs the use case, **never answered — even when it fails**; alone, HTTP 204 |
| `"id": null` | a call, answered with `"id": null` (the specification discourages it, it does not forbid it) |
| An `id` that is not a string, a number or null | `-32600`, `"id": null`, nothing runs |
| An invalid Request object without `id` | answered `-32600` — it is not a notification |
| `[]` | one `-32600`, not an array |
| A batch | each item answered on its own (an invalid item gets its own `-32600`); the calls may run and answer in any order |
| A batch of only notifications | nothing — HTTP 204 |
| A batch over `max_batch_size` | one `-32600` (`data.reason` `BATCH_TOO_LARGE`, `data.limit`), **nothing runs** |

**By name only.** Every method is published `paramStructure: "by-name"`: `params` is an object
whose members are the DTO's fields (or omitted). An array is answered `-32602 Invalid params`
(`data.reason` `PARAMS_BY_POSITION`), so the specification's positional examples
(`"params": [42, 23]`) are refused, and their by-name form answers as the specification does.
A `result` is always the declared response rendered as an object — a Feature returning `19`
answers `{"result": 19}`. Every example of the specification's §7 is a golden test
(`tests/entrypoint/test_jsonrpc_conformance.py`).

**`rpc.` is the protocol's.** Method names beginning `rpc.` are reserved by JSON-RPC 2.0; the
only one served is `rpc.discover`. A bus aliased `rpc` (which would publish `rpc.issue_invoice`)
is refused when it is added, and a declared name or a group namespace under `rpc.` fails the
build.

---

## HTTP — limits, statuses, content type

| Situation | HTTP | Body |
|---|---|---|
| Any JSON-RPC answer, errors included | 200 | the answer |
| Nothing to answer (a notification, a batch of them) | 204 | empty |
| A lone request refused as unauthenticated | 401 + `WWW-Authenticate` | the `-32001` answer |
| A body over `max_body_bytes` | 413 | a `-32600` answer, `data.reason` `BODY_TOO_LARGE` |
| A `Content-Type` other than `application/json` / `application/json-rpc` (parameters such as `charset` allowed), or none | 415 | a `-32600` answer, `data.reason` `UNSUPPORTED_MEDIA_TYPE` |

Both limits are the gateway's, and published in the document as `x-sincpro-limits`:

```python
RpcGateway({"qr": qr})                                        # 50 calls, 1 MiB
RpcGateway({"qr": qr}, max_batch_size=10, max_body_bytes=64 * 1024)
build_rpc_app({"qr": qr}, max_batch_size=10)
```

A body with a `Content-Length` over the limit is refused before a byte of it is read; a body
streamed without one is counted as it arrives and refused the moment it passes the limit.
`handle()` (in process, no HTTP) enforces the batch limit too; the body limit is checked by
`dispatch` on a raw body (`bytes`), and by the route while it streams.

The limits, the title and the version belong to the wire: given a `port=JsonRpcWire(...)`, they
are the port's, and passing them to the gateway as well is refused.

---

## Full control — `dispatch`

A route of the project's, under any framework, runs **the same path** the gateway's route runs —
the gateway's own route calls `dispatch` too (PRD_15 §1.2; a parity test compares both):

```python
from sincpro_framework.auth import credentials_from_asgi
from sincpro_framework.entrypoints.rpc import RpcGateway, dispatch, http_status

surface = RpcGateway({"billing": billing}).build()     # validated once, at startup

async def rpc_endpoint(request):
    reply = await asyncio.to_thread(                     # auth and the bus: one thread
        dispatch,
        surface,
        await request.body(),                            # bytes: size-checked, then parsed
        credentials=credentials_from_asgi(request.scope),
        headers=request.headers,                         # X-Correlation-Id, traceparent
    )
    status, headers = http_status(surface, reply)        # 200 / 204 / 401 + WWW-Authenticate / 413
    if reply is None:
        return Response(status_code=status, headers=headers)
    return JSONResponse(reply, status_code=status, headers=headers)
```

| Symbol | What |
|---|---|
| `dispatch(surface, payload, *, credentials=, headers=, context=)` | the reply, or `None` when nothing is answered. `surface` is `gateway.build()` (its limits) or `gateway.surface()` (the default limits); `payload` is the raw body as `bytes` or the parsed JSON; `context` goes over the headers' context, the body's `context` over both |
| `http_status(surface, reply)` | `(status, headers)` for the reply |
| `is_json_media_type(content_type)` | the 415 check the route makes before reading |

`dispatch` calls the bus, never `execute`: the access guard, idempotency, caching, the span and
Sentry apply by construction. Call it in the thread the bus runs in — identity is a
`ContextVar`.

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
| `RpcGateway(buses, layers=, title=, version=, max_batch_size=50, max_body_bytes=1 MiB, *, exposure=DECLARED, unguarded=False, port=None)` | Composition root. Several buses, one process. |
| `.add(alias, bus, include=, exclude=, wrap=)`, `.group(bus, namespace=, version=)`, `.override(...)`, `.bind(...)` | The composition (PRD_14). |
| `.surface()`, `.manifest()`, `.verify()` | The validated surface, as data; the reasons it would be refused. |
| `.build()` | The `RpcSurface` served — methods, limits, document. |
| `.methods()` | Each published method by name. |
| `.handle(payload, context=, credentials=)` | In-process JSON-RPC (tests, workers) — `dispatch` over `.build()`. |
| `.discover()` | OpenRPC 1.4 document. |
| `.routes(rpc_path=, discover_path=, health_path=)` | This gateway's `Route`s (health included), for a host's own app. |
| `.app(middleware=, routes=, lifespan=, health_path=, **kwargs)` | Starlette: `POST /rpc`, `GET /openrpc.json`, `GET /healthz`, composable. |
| `.health_route(path=)`, `.is_healthy()` | The health route; the check itself. |
| `.run(host=..., port=...)` | uvicorn. |
| `build_rpc_app(buses, ..., exposure=, unguarded=)` | Same as `RpcGateway(...).app()`. |
| `JsonRpcWire(title=, version=, max_batch_size=, max_body_bytes=)` | The wire: derivation, the wire's refusals, `build`. |
| `dispatch`, `http_status`, `operation_name` | Full control; the operation of a DTO name. |

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
        ├── __init__.py    # re-exports RpcGateway, build_rpc_app, JsonRpcWire, dispatch, ...
        ├── errors.py      # the error catalogue: codes, `data` (kind, reason, retryable), components.errors
        ├── jrpc.py        # the protocol: requests, batches, notifications, OpenRPC document
        ├── wire.py        # JsonRpcWire (names, refusals, build), RpcSurface, dispatch, http_status
        └── entrypoint.py  # RpcGateway: the core Gateway on the "rpc" wire, and the Starlette routes
```

Same split as `entrypoint_mcp`: `entrypoint.py` orchestrates, `jrpc.py` holds everything specific to the JSON-RPC wire. A future protocol under `entrypoints/` follows the same two-file recipe. `jrpc.py`'s `execute()` (context/tracing around a call) comes from `scalar_executor.py`, not from `catalog.py` — it is orthogonal to packaging metadata into a wire DTO, and MCP does not need it (FastMCP has no per-request context sibling field the way JSON-RPC does).

---

## Constraints

| Rule | Why |
|---|---|
| No JSON-RPC types on Feature | Hexagonal. |
| Do not share FastMCP's HTTP port | MCP methods would collide. |
| Alias has no dots, and is not `rpc` | It is the default namespace; `rpc.` is the protocol's prefix. |
| No layer in a name | A Feature promoted to an ApplicationService renames nothing public. |
| Declared by default | Nothing is public by forgetting; `Exposure.CATALOG` is asked for. |
| `params` is a JSON object | By-name (`paramStructure: "by-name"`), same shape as MCP arguments. An array is `-32602`. |
| Starlette/uvicorn stay an extra | Core bus installs without an RPC stack. |
