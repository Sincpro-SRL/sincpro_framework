# Bounded contexts across services

A service imports a bounded context and executes a use case:

```python
from my_erp.billing import billing
answer = billing(CommandIssueInvoice(total=50, pdf=b"%PDF-1.7..."), ResponseIssueInvoice)
```

When the **context map** says `billing` is hosted by another service, that call does not run here:
the Command goes to that service, runs on its own `billing` bus, and the answer (or the error it
raised) comes back synchronously. **The caller's code does not change — only configuration.**
Full depth: `docs/entrypoints/bounded-contexts-across-services.md`.

```yaml
# the conf file
context_map:
  - context: billing
    at: grpc://billing-service:50051?timeout=5
  - context: catalog
    at: http://catalog-service:8000
```

```bash
SINCPRO_CONTEXT_MAP="billing=grpc://10.0.0.5:50051"      # the environment wins, per context
```

### Where a context runs — and which wins

**Nothing configured: every context runs here**, built as always. Only something that says where
it is hosted makes it a reference:

| How | Written | Use it for |
|---|---|---|
| nothing | — | dev, one process, every existing project |
| configuration | `context_map:` / `SINCPRO_CONTEXT_MAP` | **production services** — prefer this |
| code | `billing.hosted_by("grpc://b:50051")` or `hosted_by(HostedAt(Wire.GRPC, "b:50051", timeout=5))` | tests, scripts, a hand-wired composition root |
| forced local | `billing.run_here()` — `serve` / `open_host` / `open_host_routes` do it | runs here whatever was said |

The last one applied wins: configuration at creation (`SINCPRO_CONTEXT_MAP` over the conf file,
per context) → `hosted_by(...)` replaces it (**the environment can no longer move an address fixed
in code**) → `run_here()` / serving wins over both. Don't hard-code `hosted_by` in a service's
production wiring — leave the address to its configuration. `billing.hosted_at`,
`billing.is_reference` tell what was decided.

Typed: the URL is read into `HostedAt(wire: Wire, address: "host:port", timeout)` — `Wire` is a
`StrEnum` (`GRPC`, `HTTP`, `HTTPS`). `context_map:` entries are `HostedContext`, validated when the
conf file loads (errors at `context_map.N.at`); `SINCPRO_CONTEXT_MAP` errors name the variable.
A wrong address raises `InvalidAddress` saying what to write.

On the caller `billing` is a **reference** (`billing.is_reference`): a client with the face of the
bus — never built, no Feature instantiated, no dependency resolved, nothing emitted (the real bus
emits outcomes and events where it runs). Inject it as any bus: `sales.add_dependency("billing",
billing)`, then `self.billing(cmd, Response)` in an ApplicationService.

**Local unless this process says otherwise.** Each deployment names only what it reaches elsewhere —
the service hosting `billing` does not name it, so its server, workers and crons all run it. `SINCPRO_CONTEXT_MAP` is read from the environment even when the
project's conf file does not declare it, and a reference logs where it forwards when created. What a
process serves runs there even if its own map names it (a safety net: never calls itself).

`build_root_bus()` never fails on a reference: it returns at once (`was_initialized` stays `False`,
`is_ready` is `True`). A local bus builds once, under a lock.

## Hosting a context — the Open Host Service

```python
from sincpro_framework.remote_execution import open_host_routes

billing.serve("0.0.0.0:50051")                             # its own deployment: blocks
host = billing.serve("0.0.0.0:50051", Attach.THREAD)       # beside a REST API, one process
host = billing.serve("0.0.0.0:50051", Attach.PROCESS)      # a subprocess of its own
app.router.routes.extend(open_host_routes([catalog]))      # on the REST API's own port
serve_contexts([billing, catalog], address, attach)
```

An `OpenHost` has `.address`, `.pid`, `.stop(grace=5)`. In a FastAPI lifespan, start in the yield
and `host.stop()` after.

## Guarantees

1. **Zero code change on the caller** — sync, async, a `Subscriber`, a catalog: all forward.
2. **A reference is never built**, by any path — its registrations answer what is asked of it.
3. **One line on the host**, in the shape its deployment already has.
4. **Typed values, any size that fits in memory** — `bytes`, `Decimal`, `datetime`, `UUID`, enums
   travel as they are (no JSON/base64), in 1 MiB chunks; rebuilt by the DTO's own class.
5. **The answer is the DTO, never a `dict`** — `Response` given, else the handler's declared type,
   else the class the host named (a handler declaring `Any`).
6. **No version validation** — each team deploys at its own pace; a DTO that fits is answered
   (unknown fields ignored, defaults filled). Only a DTO that does not fit fails.
7. **The error comes back as itself** — its class, `args` and attributes (`ContextRequired.missing`).
8. **The whole context and the trace cross over**, with their types, `Secret` included — the
   framework filters nothing; what goes in the context is the project's call.
9. **Bounded in time** — each call has a deadline (address's `timeout`, default 30 s).

## Transports

| | gRPC `grpc://` | HTTP `http://`, `https://` |
|---|---|---|
| host | `serve(...)` — own port | `open_host_routes(...)` on the ASGI app |
| wire | `/sincpro.Contexts/Execute`, streaming 1 MiB chunks | `POST /sincpro/contexts/execute` |
| caller's client | `grpcio` (`[grpc]`) | standard library — no extra |
| choose when | its own port, or the fastest path | already behind a REST API / ingress / TLS |

## Errors on the caller

| What happened | On the caller | Kind |
|---|---|---|
| a class the caller imported | that class, with its `args` and attributes | its own |
| a class the caller did not import | `ContextFailed` (`.kind` names it) | `internal` |
| the DTO or the answer does not fit | `DTODoesNotFit` (`.fields`) | `invalid` |
| the host does not answer that DTO (renamed/moved) | `UnknownDTOToExecute` | `internal` |
| nobody answering / not hosting it — it did not run | `ContextUnavailable` | `unavailable` |
| the host took the call, the connection was lost | `ContextOutcomeUnknown` | `unknown_outcome` |
| the deadline passes | `ContextTimeout` (an `ContextOutcomeUnknown`) | `unknown_outcome` |

`unknown_outcome` — it may have run: verify, or retry with an idempotency key, before doing it
again or undoing it.

## What it does not do

- **No retries** (a retried Command that already wrote would write twice).
- **No discovery or load balancing** (an address is an address).
- **No security of its own** — who the call acts for crosses as credentials; TLS, mTLS, a gateway
  and what goes in the context are the project's.

Python on both ends; another language uses the gateway's `Struct` methods and `.proto` export.
Values only — anything outside the allow-list raises `CannotTravel`.
