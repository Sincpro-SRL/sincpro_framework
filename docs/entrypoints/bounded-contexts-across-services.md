# Bounded contexts across services

A service imports a bounded context exactly as it always does, and executes a use case:

```python
from my_erp.billing import billing
from my_erp.billing.services.issue_invoice import CommandIssueInvoice, ResponseIssueInvoice

answer = billing(CommandIssueInvoice(total=50, pdf=b"%PDF-1.7..."), ResponseIssueInvoice)
```

When the **context map** says `billing` is hosted by another service, that call does not run here:
the Command goes to that service, it runs on its own `billing` bus, and the answer — or the error
it raised — comes back, synchronously. The code does not change; only the configuration:

```yaml
# the conf file (SINCPRO_FRAMEWORK_CONFIG_FILE)
context_map:
  - context: billing
    at: grpc://billing-service:50051?timeout=5
  - context: catalog
    at: http://catalog-service:8000
```

```bash
SINCPRO_CONTEXT_MAP="billing=grpc://10.0.0.5:50051"      # the environment wins, per context
```

The service that hosts a context — its **Open Host Service** — does it in one line, however that
service runs:

```python
billing.serve("0.0.0.0:50051")                                   # its own deployment: blocks
host = billing.serve("0.0.0.0:50051", Attach.THREAD)             # beside a REST API, one process
host = billing.serve("0.0.0.0:50051", Attach.PROCESS)            # a subprocess of its own
app.router.routes.extend(open_host_routes([catalog]))            # or on the REST API's own port
```

## Why this exists

A bounded context is written once and deployed however the load asks: all in one process, or
billing on its own machine because it signs documents, or catalog next to its database. A use case
of one context calling another should not be rewritten when the deployment changes —
`billing(dto)` is the API whether billing runs here or elsewhere.

That is what DDD's **context map** is for: it says where each bounded context lives and how the
others reach it. A context others consume through a protocol of its own is an **Open Host
Service**. Axon's distributed command bus, Dapr's service invocation and Orleans' location
transparency make the same promise: the bus is the API, where it runs is configuration.

## What it guarantees

1. **Zero code change on the caller.** A context the map places elsewhere forwards every execution;
   `bus(dto, Response)` answers what it would have answered locally. Its async facade too.
2. **One line on the host**, in the shape its deployment already has: blocking, in a thread beside a
   REST API, in a subprocess of its own, or as a route on the REST API's own port.
3. **Typed values, any size that fits in memory.** A DTO travels with its `bytes`, `Decimal`,
   `datetime`, `UUID` and enum values as they are — no JSON, no base64 — in chunks of 1 MiB both
   ways, so there is no per-message cap: several GiB travel as long as they fit in memory on both
   ends. It is rebuilt by its own class, each dataclass through its own constructor — a
   SQLAlchemy-mapped aggregate gets its instance state, a change-tracked one its baseline.
4. **The error comes back as itself.** An exception whose class the caller has imported —
   `ContractViolation`, the project's own `InvalidRequest`, a `ValueError` — is raised as that class,
   with its message, so `except InvalidRequest` works across services.
5. **The request context and the trace cross over**, with their types: what `bus.context({...})`
   set is what the host's handlers read in `self.context`, and the host's spans land in the caller's
   trace.
6. **Bounded in time.** Each call has a deadline — the address's `timeout`, 30 seconds when not said.
7. **No loop.** Two services often share an environment; the one hosting `billing` may read a map
   that places `billing` at itself. It answers the call it is hosting instead of forwarding it — and
   a handler there that calls *another* context hosted elsewhere still reaches it.
8. **Open by default.** Only what cannot work is refused: an address no transport can reach, a
   context nobody answers for (at the call). An unknown option in an address is a warning.

## The context map

```text
context_map:                        the conf file — a list
  - context: <bus name>
    at: <address>
SINCPRO_CONTEXT_MAP = <bus>=<address>[,<bus>=<address>…]      the environment — wins per context
address = grpc://<host>:<port>[?timeout=<seconds>]
        | http://<host>:<port>[?timeout=<seconds>]
        | https://<host>:<port>[?timeout=<seconds>]
```

- The name is the bus's own — the one given to `UseFramework("billing")`.
- Read when the bus is created. A context the map does not name runs in this process.
- In code — a test, a composition root that prefers wiring to environment — the same thing is
  `billing.hosted_by("grpc://10.0.0.5:50051?timeout=5")`; `billing.hosted_at` says where it is
  hosted, or `None`.

## Hosting a context: the Open Host Service

| | How | When |
|---|---|---|
| `billing.serve(address)` | blocks until SIGTERM/SIGINT, draining the calls in progress | the context has a deployment of its own |
| `billing.serve(address, Attach.THREAD)` | gRPC on this process's threads; answers an `OpenHost` at once | beside a REST API in the same process — a FastAPI lifespan starts and stops it |
| `billing.serve(address, Attach.PROCESS)` | a subprocess of its own, tied to this one; answers once it listens | the context wants its own CPU, GIL and crash, still one deployment |
| `open_host_routes([catalog])` | one route, `POST /sincpro/contexts/execute`, on the ASGI app the service already has | no second port: the same address, ingress and TLS as the REST API |
| `serve_contexts([billing, catalog], address, attach)` | several contexts on one host | |

```python
from contextlib import asynccontextmanager

from sincpro_framework.remote_execution import Attach


@asynccontextmanager
async def lifespan(app):
    host = billing.serve("0.0.0.0:50051", Attach.THREAD)
    yield
    host.stop()                          # stops taking calls, drains the ones in progress
```

An `OpenHost` has `.address` (its bound port when `:0` was asked for), `.pid` (the process that
answers) and `.stop(grace=5)`.

`Attach.PROCESS` launches `python -m sincpro_framework.remote_execution.entrypoint.host_process`, which imports what
it hosts by path — `"my_erp.billing:billing"`; a bus object is turned into one by finding the module
that holds it. It never re-imports this process's `__main__`, so a script needs no
`if __name__ == "__main__"` guard. It stops with `stop()`, and on its own when this process dies:
its stdin closes. Its logs come out on this process's stdout.

## Two transports, and when to choose each

| | gRPC — `grpc://` | HTTP — `http://`, `https://` |
|---|---|---|
| host | `serve(...)` — its own port | `open_host_routes(...)` on the REST API's app |
| wire | `/sincpro.Contexts/Execute`, client and server streaming of 1 MiB chunks | `POST /sincpro/contexts/execute`, chunked request, streamed response |
| caller's client | `grpcio` (the `[grpc]` extra) | the standard library — no extra |
| deadline | the whole call | each read and write |
| choose it when | the context has its own port, or the fastest path matters | the context already sits behind a REST API, ingress, TLS or a gateway |

Measured on one machine: 64 MiB round trip in 0.8 s, 256 MiB in 4.2 s over gRPC.

## How a call travels

1. `billing(dto, Response)` finds `billing` in the context map.
2. The DTO is dumped in pydantic's python mode — its values as Python keeps them, `bytes` included —
   and `pickle`d into 1 MiB chunks; a large `bytes` value is handed over as itself and cut, never
   copied whole. The context's name, the DTO's registered name, the request context (packed the
   same way) and the trace (`traceparent`) ride as metadata or headers.
3. On the host the payload is read as it arrives, through an **allow-list** — builtin containers
   and scalars, `bytes`, `datetime`/`date`/`time`/`timedelta`, `Decimal`, `UUID`, enum members — and
   rebuilt by the DTO's own class. It is executed inside the caller's request context and trace.
4. The answer is finished before its first chunk leaves, so a failure is answered as one — never as
   a cut stream — and it travels back the same way; the caller rebuilds it with the response class
   it asked for, or the one its handler declares.

**Why not JSON:** pydantic writes `bytes` as UTF-8, so a PDF fails, and a base64 string cannot be
told from a text one on the way back. **Why `pickle` is safe here:** only values are packed — never
objects of the project — and the receiver refuses, while reading and before anything is built, any
class outside the allow-list; no code runs on receipt.

## Errors

| On the host | On the caller |
|---|---|
| an exception whose class the caller has imported, built from a message | the same class, with the host's message |
| one the caller has not imported, or that needs more than a message | `ContextFailed` — `.kind` names the host's class |
| nobody answering, or not hosting that context | `ContextUnavailable` |
| the deadline passes | `ContextTimeout` |

The caller never imports a module because the host named it: the class is looked up among what the
caller already imported. The host still logs and reports its own failure.

## What to know before relying on it

- **The same code on both ends.** A DTO is found by its module path and rebuilt by its class; a
  rolling deploy that adds a field gives it a default, as with any other client of the code.
- **Python on both ends.** `/sincpro.Contexts/Execute` and `/sincpro/contexts/execute` carry Python's
  values; another language uses the gateway's `Struct` methods and its `.proto` export.
- **Values only.** Anything outside the allow-list raises `CannotTravel` on the receiving side.
- **Memory, not the wire, is the limit.** A payload is held once on each end; one larger than memory
  belongs in storage, with a reference in the DTO — the claim check pattern.
- **Opaque to generic tools.** The bodies are bytes: `grpcurl` and reflection see the method, not
  the fields.

## What it does not do

- **No retries.** A retried Command that already wrote would write twice; retrying is the caller's
  decision, or the infrastructure's — a mesh, Dapr.
- **No discovery or load balancing.** An address is an address; a Kubernetes Service, a gateway or a
  load balancer behind it does that.
- **No authentication.** Security is the deployment's: TLS, mTLS, or a gateway in front of the host.

## Reference

| | |
|---|---|
| `context_map` (conf file), `SINCPRO_CONTEXT_MAP` | where contexts are hosted, by name |
| `bus.hosted_by(address)`, `bus.hosted_at` | the same, in code |
| `bus.serve(address, attach)`, `serve_contexts([...], address, attach)`, `Attach`, `OpenHost` | the Open Host Service over gRPC |
| `open_host_routes([...])` | `sincpro_framework.remote_execution.entrypoint.http` — the Open Host Service on an ASGI app |
| `ContextFailed`, `ContextUnavailable`, `ContextTimeout`, `CannotTravel` | `sincpro_framework.remote_execution` |
| `open_host([...])` → `.server()` / `.mount(server)` | the gRPC open host as a server of its own (what `bus.serve` runs: the door and its health, never the contexts' public catalog), or mounted on a server of the caller's — beside a public `GrpcGateway` on one port, on purpose |

## Module map

`sincpro_framework.remote_execution`, laid out as `cron` and `migrations` are:

| Module | What it holds |
|---|---|
| `domain/address.py` | `HostedAt`, where a context runs, and parsing an address or a context map |
| `domain/payload.py` | the published language: `pack` / `packed`, `unpack` / `unpacked`, the allow-list, `ChunkReader` |
| `domain/errors.py` | `ContextFailed`, `ContextUnavailable`, `ContextTimeout`, and raising a remote exception as itself |
| `domain/transport.py` | `Transport`, the port a transport implements |
| `domain/hosting.py` | the contexts this execution hosts — they run here, whatever the configuration says |
| `adapters/http.py` | the caller's side over HTTP — the standard library, no extra |
| `adapters/grpc.py` | the caller's side over gRPC — `[grpc]`, imported only when an address names it |
| `adapters/__init__.py` | `transport_for(address)`, chosen by the scheme |
| `configuration.py` | the context map, read from the conf file and `SINCPRO_CONTEXT_MAP` |
| `entrypoint/hosts.py` | `serve_contexts`, `Attach`, `OpenHost` — what `bus.serve` runs |
| `entrypoint/http.py` | `open_host_routes`, the HTTP Open Host — `[rpc]` |
| `entrypoint/grpc.py` | `open_host`, the gRPC Open Host — its own server, or mounted on one — `[grpc]` |
| `entrypoint/execution.py` | the execution both Open Hosts run |
| `entrypoint/host_process.py` | the subprocess `Attach.PROCESS` launches |

The core imports `remote_execution` and runs a local bus with every extra missing; an `http://`
address needs nothing but the standard library, a `grpc://` one raises the `[grpc]` `ImportError`
at the call (`tests/test_core_without_extras.py`).
