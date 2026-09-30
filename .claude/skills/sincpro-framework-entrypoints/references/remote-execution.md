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

In code: `billing.hosted_by("grpc://…")`, `billing.hosted_at`. Read when the bus is created.

## Hosting a context — the Open Host Service

```python
billing.serve("0.0.0.0:50051")                             # its own deployment: blocks
host = billing.serve("0.0.0.0:50051", Attach.THREAD)       # beside a REST API, one process
host = billing.serve("0.0.0.0:50051", Attach.PROCESS)      # a subprocess of its own
app.router.routes.extend(open_host_routes([catalog]))      # on the REST API's own port
serve_contexts([billing, catalog], address, attach)
```

An `OpenHost` has `.address`, `.pid`, `.stop(grace=5)`. In a FastAPI lifespan, start in the yield
and `host.stop()` after.

## Guarantees

1. **Zero code change on the caller** — a context the map places elsewhere forwards every execution.
2. **One line on the host**, in the shape its deployment already has.
3. **Typed values, any size that fits in memory** — `bytes`, `Decimal`, `datetime`, `UUID`, enums
   travel as they are (no JSON/base64), in 1 MiB chunks; rebuilt by the DTO's own class.
4. **The error comes back as itself** — an exception the caller has imported is raised as that class.
5. **Request context and trace cross over**, with their types.
6. **Bounded in time** — each call has a deadline (address's `timeout`, default 30 s).
7. **No loop** — the host answers a context it hosts instead of forwarding to itself.
8. **Open by default** — only what cannot work is refused.

## Transports

| | gRPC `grpc://` | HTTP `http://`, `https://` |
|---|---|---|
| host | `serve(...)` — own port | `open_host_routes(...)` on the ASGI app |
| wire | `/sincpro.Contexts/Execute`, streaming 1 MiB chunks | `POST /sincpro/contexts/execute` |
| caller's client | `grpcio` (`[grpc]`) | standard library — no extra |
| choose when | its own port, or the fastest path | already behind a REST API / ingress / TLS |

## Errors on the caller

| On the host | On the caller |
|---|---|
| a class the caller imported | that class, with the host's message |
| a class the caller did not import | `ContextFailed` (`.kind` names it) |
| nobody hosting that context | `ContextUnavailable` |
| the deadline passes | `ContextTimeout` |

## What it does not do

- **No retries** (a retried Command that already wrote would write twice).
- **No discovery or load balancing** (an address is an address).
- **No authentication** — security is the deployment's (TLS, mTLS, a gateway).

Python on both ends; another language uses the gateway's `Struct` methods and `.proto` export.
Values only — anything outside the allow-list raises `CannotTravel`.
