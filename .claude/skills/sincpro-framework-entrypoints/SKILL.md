---
name: sincpro-framework-entrypoints
description: Expose a sincpro_framework bus over a transport — REST/FastAPI, JSON-RPC 2.0, gRPC, MCP, a queue, or one bounded context hosted by another service. Use whenever a task publishes, serves or mounts the bus (HTTP routes, RPC methods, gRPC services, MCP tools, a Kafka/RabbitMQ consumer, a context map), or adds declared exposure (@rest/@rpc/@grpc/@mcp/@queue/@internal).
---

# sincpro-framework-entrypoints

A `UseFramework` already knows every DTO it answers and what each means. An entrypoint publishes
that catalog over a transport **without the domain learning about the transport**. They are extras:
`[rest]`, `[rpc]`, `[grpc]`, `[mcp]`, `[fastapi]`, `[faststream]`. A service that only runs a bus
installs none.

Depth: `docs/entrypoints/README.md` plus `integration.md`, `rest.md`, `rpc.md`, `grpc.md`, `mcp.md`,
`fastapi.md`, `queue.md`, `bounded-contexts-across-services.md`.

## The one rule

**The domain does not know the wire.** No `expose_mcp=True`, no transport types in `services/`. A
gateway reads the bus and marshals DTOs; `gateway(...)` calls the bus, never `execute`.

## The one base, every wire

```python
from sincpro_framework.entrypoints.rest import RestGateway
from sincpro_framework.entrypoints.rpc import RpcGateway
from sincpro_framework.entrypoints.grpc import GrpcGateway
from sincpro_framework.entrypoints.mcp import McpGateway

RestGateway([billing, sales], prefix="/api/v1", title="Billing API")
RpcGateway({"billing": billing}).run()
GrpcGateway([billing]).run("0.0.0.0:50051")
McpGateway([billing, sales]).server().run()
```

Every Feature and ApplicationService of every bus is published, each bus under its own name made fit
for the wire. `{"billing": billing}` names the alias instead — do that for a public wire, so renaming
the bus never moves a URL or a tool.

## Two ways to say what is published

- **Catalog** (the older default): every use case, narrowed with `add(bus, include=, exclude=,
  wrap=)`, `layers=`, or `@internal` (on no wire, ever).
- **Declared exposure** (PRD_14, the default for the new gateways): only what a use case binds to a
  wire. The decorators import no transport library, so `services/` stays clean:

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
