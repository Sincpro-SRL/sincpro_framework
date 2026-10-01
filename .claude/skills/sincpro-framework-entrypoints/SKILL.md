---
name: sincpro-framework-entrypoints
description: Expose a sincpro_framework bus over a transport — REST/FastAPI, JSON-RPC 2.0, gRPC, MCP, a queue, or one bounded context hosted by another service. Use whenever a task publishes, serves or mounts the bus (HTTP routes, RPC methods, gRPC services, MCP tools, a Kafka/RabbitMQ consumer, a context map), or adds declared exposure (@rest/@rpc/@grpc/@mcp/@queue/@internal). Read it before adding a route or endpoint to Features/ApplicationServices that already exist, before writing a FastAPI route by hand, and before uploading a file over HTTP.
---

# sincpro-framework-entrypoints

A `UseFramework` already knows every DTO it answers and what each means. An entrypoint publishes
that catalog over a transport **without the domain learning about the transport**. They are extras:
`[rest]`, `[rpc]`, `[grpc]`, `[mcp]`, `[fastapi]`, `[faststream]`. A service that only runs a bus
installs none.

Deep docs, in the framework repo (not shipped with the package; this skill stands without them):
`docs/entrypoints/` — `fastapi.md`, `integration.md`, `rpc.md`, `grpc.md`, `mcp.md`, `queue.md`,
`bounded-contexts-across-services.md`.

## Context

A service's use cases already live on its buses. Callers outside the process (a browser, another
service, an LLM agent, a broker) need them over a protocol. An entrypoint is the **driving
adapter** of the hexagon: it turns a request into the DTO, calls the bus, and turns the answer or
the failure into the protocol's shape. It holds no business logic and no use cases of its own.
It is not where a use case is defined, composed or adapted. That happens in `services/`.

## Abstractions

| Term | What it is | Kind | Import |
|---|---|---|---|
| **wire** | One protocol: `rest`, `rpc`, `grpc`, `mcp`, `queue` | name | — |
| **binding** | What a use case declares for one wire: path, verb, status, tool name… One per wire per class | DTO (`RestBinding`, `McpBinding`…) | `sincpro_framework.entrypoints.exposure` |
| `@rest.post(...)`, `@mcp()`, `@rpc()`, `@grpc()`, `@queue.consumes(...)` | Record a binding for the handler they decorate. They import no web library | decorator | `sincpro_framework.entrypoints.exposure` |
| `@internal` | Never on any wire, whatever else is declared | decorator | `sincpro_framework.entrypoints` |
| **gateway** | Reads N buses, resolves what is published on its wire, builds what is served | class (`FastApiGateway`, `RpcGateway`, `GrpcGateway`, `McpGateway`, `QueueGateway`) | `sincpro_framework.entrypoints.<wire>` |
| **mode** (`Exposure`) | What a gateway publishes: `DECLARED` = only use cases with a binding for its wire; `CATALOG` = every use case | enum | `sincpro_framework.entrypoints.exposure` |
| **catalog** | Every use case of one bus, narrowed by `include` / `exclude` / `layers` / `@internal`. The raw material | class `Catalog` | `sincpro_framework.entrypoints.catalog` |
| **group** | One bounded context's conventions on a wire: prefix, version, tags, namespace, package | DTO `Group`, set with `gateway.group(bus, ...)` | `sincpro_framework.entrypoints.exposure` |
| **operation** | One published use case as facts: alias, Command, handler, schemas, Query or not, access | DTO `Operation` | `sincpro_framework.entrypoints.exposure` |
| **resolved** | An operation with its final binding (precedence applied) and its group | DTO `Resolved` | `sincpro_framework.entrypoints.exposure` |
| **surface** | The list of resolved operations: what the gateway actually publishes. Validated, or `ExposureRefused` | `gateway.surface()` | — |
| **manifest** | The surface as frozen, JSON-safe rows, for a snapshot test | `gateway.manifest()` → `ManifestEntry` | — |
| **port** (`Wire`) | The interface a project implements for a transport of its own | abstract class | `sincpro_framework.entrypoints.exposure` |
| `bus_call(bus)` | FastAPI dependency: run a DTO on the bus from a hand-written route, the generated routes' way | function | `sincpro_framework.entrypoints.fastapi` |
| `failure_kind` | The class attribute that classifies an error for every wire | attribute + `FailureKind` enum | `sincpro_framework.transport.failures` |

"Exposure" is both the package of the decorators (`entrypoints.exposure`) and the mode enum
(`Exposure`). When this skill says *mode*, it means the enum.

## Architecture

**Inside the framework.** `entrypoints/exposure/` holds the decorators, bindings and the registry,
importing only the application layer. `entrypoints/gateway.py` is the one base every wire extends.
It reads catalogs, applies precedence, validates (fails closed) and hands the surface to the
wire's port. Each wire (`fastapi/`, `rpc/`, `grpc/`, `mcp/`, `faststream/`) is an optional extra
that translates the surface into its library's objects. The core never imports a web library.

**Inside a service:**

```text
my_service/
  domains/<ctx>/
    infrastructure/framework.py   bus = UseFramework("<ctx>")   + auth.on(bus), interceptors
    services/<use_case>.py        @bus.feature(Command…) + @rest.post(...) / @mcp() ← bindings here
    domain/exceptions.py          class …(DomainError): failure_kind = …
  entrypoints/
    http/app.py                   FastApiGateway({...}).app()   ← gateway, built last
    http/routes.py                only hand-written routes that call bus_call (bytes, other bodies)
    mcp/server.py                 McpGateway({...}).server()
```

**One call:**

```text
request ─▶ wire (FastAPI route) ─▶ validates DTO once ─▶ bus(dto) ─▶ auth guard, interceptors,
         idempotency, span ─▶ Feature/ApplicationService.execute ─▶ answer
                                         └─ error ─▶ failure_kind ─▶ problem+json / RPC code / gRPC status
```

**Bootstrap order:** bus created → services imported (decorators register) → `auth.on(bus)`,
interceptors, handlers → gateway created (this builds the bus; nothing more can be registered).

## The one rule

**The domain does not know the wire.** No `expose_mcp=True`, no transport types in `services/`. A
gateway reads the bus and marshals DTOs; `gateway(...)` calls the bus, never `execute`.

The exposure decorators (`@rest`, `@rpc`, `@grpc`, `@mcp`, `@queue`) are **not** transport types:
they import no web library and they go **on the existing Feature/ApplicationService in
`services/`**. That is how the domain says "publish this" without knowing HTTP.

## Quick start: the buses exist, add REST routes

Two edits, nothing else:

```python
# 1. domains/billing/services/issue_invoice.py — on the handler that already exists
from sincpro_framework.entrypoints.exposure import rest

@billing.feature(CommandIssueInvoice)
@rest.post("/invoices", status=201)
class IssueInvoice(Feature): ...

@billing.feature(QueryInvoice)
@rest.get("/invoices/{invoice_id}")      # {invoice_id} must be a field of QueryInvoice
class GetInvoice(Feature): ...

# 2. entrypoints/http/app.py — the only new file
from sincpro_framework.entrypoints.fastapi import FastApiGateway

api = FastApiGateway({"billing": billing}, title="Billing API", unguarded=True)
api.group(billing, version="v1")         # /v1/billing/invoices
app = api.app()                          # routes, OpenAPI, RFC 9457 problems, /healthz
```

`uvicorn my_service.entrypoints.http.app:app`. `unguarded=True` is only for a bus with no
`AccessControl` (see `sincpro-framework-auth`). `FastApiGateway` is the REST host; `RestGateway`
(Starlette) is frozen and only kept for older services.

## Mistakes an agent makes

- **Registering a Feature, an ApplicationService or a bus in `entrypoints/`** to get a route
  (`@bus.feature(BodyDto)` wrapping the real Command, `UseFramework(...)` for HTTP). That creates
  a second surface that MCP, RPC and the tests do not see, and it only exists if the router
  module happened to be imported. Instead, decorate the existing handler, or write a
  hand-written route with `bus_call`.
- **Putting the decorators in `entrypoints/`** because "the domain must not know the wire". The
  decorators are not transport. They go on the handler in `services/`.
- **Writing `@app.exception_handler(...)`.** The answer then contradicts the OpenAPI document and
  the other wires. Instead, use `DomainError` + `failure_kind` (below).
- **A plain `Exception` for a refusal the caller should read.** It gets the status but no
  message. Subclass `DomainError`.
- **`group(bus, prefix="")` to drop the alias, without knowing it.** `prefix=None` (the default)
  gives `/{alias}`, `prefix=""` gives no segment, and `version` comes before both:
  `/{version}{prefix}{path}`.
- **Creating the gateway before `auth.on(bus)` / interceptors.** The gateway builds the bus, and
  later registration raises `BusAlreadyBuilt`.
- **Turning off `health_path` / `metrics_path` and rewriting `/health`.** `app()` gives
  `/healthz`, plus `/metrics` when Prometheus records. Add a readiness route only for checks of
  your own (a binary, a model).
- **Calling `execute` from a route.** That skips auth, idempotency, the span and Sentry. Call the
  bus (`bus_call`, or `bus(dto)` in-process).

## When the route cannot be generated: a hand-written route + `bus_call`

What a wire can carry is the wire's (`Wire.carries_bytes`). The built-in gateways carry JSON
today, the gRPC gateway included (`Struct`, unary), so a Command with a `bytes` field (a file, an
image) gets no generated route. A `@rest.post` (or `@mcp()`, `@rpc()`, `@grpc()`…) on its handler
is **refused** on such a wire: `verify()` names it and the build raises `ExposureRefused`. Remove
that binding; don't wrap the Command in a new Feature. (Python service to Python service,
`remote_execution` carries `bytes` as they are, over a gRPC stream — no route needed.) The same applies when the client's body is not the Command (base64 instead of bytes,
renamed fields). The Command and its Feature stay
as they are. The route goes in `entrypoints/`, translates the body into the Command and calls the
bus through `bus_call`, which runs the same path as a generated route (auth, idempotency, span,
Sentry):

```python
from fastapi import APIRouter, Depends
from sincpro_framework.entrypoints.fastapi import BusCall, bus_call, problem_responses

uploads = APIRouter(prefix="/v1/documents", tags=["documents"])

@uploads.post("/measure", operation_id="CommandMeasureDocument",
              responses=problem_responses(documents, CommandMeasureDocument))
async def measure(body: DocumentBody, call: BusCall = Depends(bus_call(documents))) -> Measured:
    return await call(CommandMeasureDocument(content=body.content()))   # translate, then call

app = api.app()
app.include_router(uploads)
assert api.verify(app) == []             # also catches a hand-written route colliding with a generated one
```

The route translates and calls; it orchestrates nothing. Two use cases chained together are an
ApplicationService in `services/`. Runnable version, in the framework repo: `docs/entrypoints/fastapi.md`, "A DTO that
cannot travel as JSON".

## Errors: declare them, don't handle them

A project's exception subclasses `DomainError` and declares `failure_kind`. Every wire maps it to
its own code (REST status, JSON-RPC code, gRPC status):

```python
from sincpro_framework.ddd.exceptions import DomainError
from sincpro_framework.transport.failures import FailureKind

class ExtractionError(DomainError):
    failure_kind = FailureKind.INVALID       # REST 422, message in `detail`

class EngineUnavailable(DomainError):
    failure_kind = FailureKind.UNAVAILABLE   # REST 503
```

Only a `DomainError`'s message reaches the caller. A plain `Exception` with `failure_kind` gets
the status but no `detail`; one without it is a 500 that says nothing.

## Every wire, one base

```python
from sincpro_framework.entrypoints.fastapi import FastApiGateway
from sincpro_framework.entrypoints.rpc import RpcGateway
from sincpro_framework.entrypoints.grpc import GrpcGateway
from sincpro_framework.entrypoints.mcp import McpGateway

FastApiGateway({"billing": billing}).app()
RpcGateway({"billing": billing}).run()
GrpcGateway([billing]).run("0.0.0.0:50051")
McpGateway([billing, sales]).server().run()
```

One gateway can carry N buses: `FastApiGateway({"billing": billing, "sales": sales})`. Name the
alias (`{"billing": billing}`) on a public wire, so renaming the bus never moves a URL or a tool.

## Two ways to say what is published

- **`DECLARED`** (the default of every gateway that names its wire): only what a use case binds
  to that wire. Nothing is public by forgetting.
- **`CATALOG`**: every use case of every bus, each one logged, narrowed with `add(bus,
  include=, exclude=)`, `layers=` or `@internal`. In `DECLARED`, `include` only narrows. It
  never publishes.

`FastApiGateway` also takes `profile`: `"resource"` (the default, `DECLARED`, REST paths) or
`"rpc"` (implies `CATALOG`, `POST /{alias}/{kebab(Dto)}`, RPC over HTTP). An explicit
`exposure=` wins over what the profile implies.

Composition on the gateway, for a binding the code does not declare: `gateway.bind(Command,
RestBinding(...))` **publishes**; `gateway.override(Command, status=202)` only **reshapes** a
binding that exists (alone, it is refused).

What `group(...)` sets, by wire: REST uses `prefix`, `version`, `tags`. JSON-RPC uses
`namespace`, `version`. gRPC uses `package`, `version`. MCP uses `prefix` (tool-name prefix).
Queue uses `prefix` (channel), `namespace` (consumer group). A field another wire reads is
ignored by this one.

The decorators import no transport library, so `services/` stays clean:

```python
from sincpro_framework.entrypoints.exposure import grpc, mcp, queue, rest, rpc

@billing.feature(CommandIssueInvoice)
@auth.requires(BillingPermission.ISSUE)
@idempotency.once(expires_after=timedelta(hours=24))
@rest.post("/invoices", status=201, location="/invoices/{number}")
@rpc()
@grpc()
@mcp(title="Emitir factura", destructive=False)
class IssueInvoice(Feature): ...

@billing.feature(QueryInvoice)
@auth.requires(BillingPermission.READ)
@rest.get("/invoices/{invoice_id}")
@mcp()                                   # read-only: it is a Query
class GetInvoice(Feature): ...
```

One binding per wire per class; a second is refused. `@internal` stays absolute. A `replaces=`
handler inherits, wire by wire, the binding of the one it replaces.

## The rules that matter

- **Gateways are made last.** Adding a bus to a gateway builds it, and a built bus takes nothing
  more — register `auth.on(bus)`, interceptors and handlers first (`BusAlreadyBuilt` otherwise).
- **The build fails closed.** A published use case on a guarded bus must declare
  `@auth.requires` / `@auth.public`; a bus with no `AccessControl` needs `unguarded=True`. A
  `{field}` in a path must be a Command field. Wrong hints, reserved names, `@internal` + a binding,
  a deprecation past its sunset — all refused, every reason at once, `ExposureRefused`.
- **The facade is handed back, not started.** `.routes()`, `.app()`, `.server()`, `.handlers()` let
  the project add middleware, interceptors, plugins. `.run(...)` is the one-liner.
- **Snapshot the surface in CI.** `gateway.manifest()` is frozen, JSON-safe and sorted; assert it
  against a snapshot so the public API only moves on purpose.
- **One failure classification, each wire's code.** `entrypoints.errors.failure_kind(error)` decides
  `invalid` / `unauthenticated` / `permission_denied` / `conflict` / `domain` / `internal`; the wire
  maps it (REST status, JSON-RPC code, gRPC status). A `DomainError`'s message reaches the caller;
  an internal failure says nothing.

## References

- [references/gateway.md](references/gateway.md) — the shared gateway: catalog, declared exposure, groups, precedence, manifest, the `Wire` port
- [references/wires.md](references/wires.md) — REST, JSON-RPC, gRPC, MCP mapping and naming
- [references/queue.md](references/queue.md) — `QueueGateway`: consuming Commands/events from a broker
- [references/remote-execution.md](references/remote-execution.md) — a bounded context hosted by another service (`serve`, context map)

## Related

- Caching and idempotency around a use case: `sincpro-framework-caching`
- Auth declarations and providers: `sincpro-framework-auth`
- The bus itself: `sincpro-framework`
