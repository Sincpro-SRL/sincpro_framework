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

1. **Zero code change on the caller.** A context the map places elsewhere is a **reference**: a
   client with the face of the bus that forwards every execution. `bus(dto, Response)` answers what
   it would have answered locally — its async facade, a `Subscriber` handing it an event, a catalog
   exposing it, an ApplicationService injected with it (`add_dependency("billing", billing)`) too.
2. **A reference is never built.** No Feature is instantiated and no dependency resolved where the
   context does not run — a `providers.Singleton(Engine)` that needs `DB_URL` is never touched. Its
   DTOs, handlers and declared responses are read from what was registered; `bus.is_reference`
   says it is one.
3. **One line on the host**, in the shape its deployment already has: blocking, in a thread beside a
   REST API, in a subprocess of its own, or as a route on the REST API's own port.
4. **Typed values, any size that fits in memory.** A DTO travels with its `bytes`, `Decimal`,
   `datetime`, `UUID` and enum values as they are — no JSON, no base64 — in chunks of 1 MiB both
   ways, so there is no per-message cap: several GiB travel as long as they fit in memory on both
   ends. It is rebuilt by its own class, each dataclass through its own constructor — a
   SQLAlchemy-mapped aggregate gets its instance state, a change-tracked one its baseline.
5. **The answer is the DTO, never a `dict`.** With `Response` given, it is an instance of it.
   Without, it is the type the handler declares; a handler declaring `Any` answers its DTOs —
   alone, in a list, in a dict — as the classes the host named.
6. **Versions are not compared — DTOs are fitted.** Each team deploys its bounded context at its
   own pace. A field the receiver does not know is ignored, a field with a default may be absent;
   only what does not fit fails, with `DTODoesNotFit` naming the fields.
7. **The error comes back as itself** — its class, its `args` and its attributes:
   `except OverCredit as refused: refused.limit`, `ContextRequired.missing`, an exception whose
   constructor takes more than a message.
8. **The request context and the trace cross over, whole.** Every value of `bus.context({...})`
   travels with its type — a `Secret`, an enum, a `Decimal`, a DTO; the framework filters nothing,
   what a project puts in its context is its decision. The host's spans land in the caller's trace.
9. **Bounded in time.** Each call has a deadline — the address's `timeout`, 30 seconds when not said.
10. **What only a network can do is named**: `ContextUnavailable` — it did not run, retry;
    `ContextOutcomeUnknown` / `ContextTimeout` — it may have run, verify before retrying or
    undoing.
11. **Local unless this process says otherwise.** Nothing configured, nothing changes: every
    context is built here, as it always was. A handler of a context served here that calls
    *another* context hosted elsewhere still reaches it.

## Where a context runs — configured, in code, or nothing at all

**By default nothing is configured, and every context runs here**, built as it always was. A
context becomes a reference only when something says where it is hosted:

| How | Written | When to use it |
|---|---|---|
| nothing | — | one process, dev, every existing project: all local |
| configuration | `context_map:` in the conf file, `SINCPRO_CONTEXT_MAP` in the environment | **services in production** — the same code runs together in dev and split in production |
| code | `billing.hosted_by("grpc://billing-svc:50051")` or `billing.hosted_by(HostedAt(Wire.GRPC, "billing-svc:50051", timeout=5))` | tests, scripts, a composition root that wires by hand |
| forced local | `billing.run_here()` — what `serve`, `open_host` and `open_host_routes` do | a process that runs the context whatever the configuration says |

**Which wins — the last one applied:**

1. Creating the bus applies the configuration: the conf file's entry, then `SINCPRO_CONTEXT_MAP`
   over it, per context.
2. `hosted_by(...)` afterwards replaces it — so an address fixed in code is one the environment can
   no longer change for that deployment.
3. `run_here()` — or serving the context — wins over both: the bus is local.

```python
billing = UseFramework("billing")                      # 1. the configuration, if any
billing.hosted_by("grpc://billing-svc:50051")          # 2. fixed in code — the environment no longer moves it
billing.serve("0.0.0.0:50051")                         # 3. served here: local, whatever 1 and 2 said
```

`billing.hosted_at` says where it is hosted (`None` when nothing does); `billing.is_reference` says
whether this process forwards to it. A code address is checked exactly as a configured one
(`InvalidAddress`), logged the same way, and kept by every generation `fresh()` makes.

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
- `SINCPRO_CONTEXT_MAP` is read from the environment whether or not the project's own conf file
  declares `$ENV:SINCPRO_CONTEXT_MAP`.
- A reference says so once, when it is created: `billing is hosted at grpc://…: this bus forwards
  every call`.
- In code, the same address is `billing.hosted_by(...)` — see the table above for which wins.

### Typed, and refused when it is read

An address is written as a URL — the one string an environment variable holds — and read into a
typed, immutable `HostedAt`, as SQLAlchemy reads a database URL into its `URL`:

| | Type | |
|---|---|---|
| `Wire` | `StrEnum` — `GRPC`, `HTTP`, `HTTPS` | the scheme; it picks the transport |
| `HostedAt` | `wire`, `address` (`host:port`), `timeout` | `HostedAt.parse(url)`, or built in code |
| `HostedContext` | `context`, `at` | one entry of `context_map:` in the settings |

The conf file's entries are validated **when it is loaded**, each error at its path; the
environment's when the first bus is created, naming the variable. Every refusal says what was
written and what to write:

```text
context_map.0.at        'grcp://b:1': 'grcp' is not a wire a context is reached by — write grpc://<host>:<port>, …
context_map.1.contxt    Extra inputs are not permitted
SINCPRO_CONTEXT_MAP     billing: 'grpc://billing-svc': 'billing-svc' is not <host>:<port> — write grpc://<host>:<port>, …
hosted_by(...)          'billing-svc:50051' names no wire — write grpc://billing-svc:50051 (or http://, https://)
```

Refused: no scheme or another one, no host, no port, a path, a `timeout` that is not seconds above
zero. An option the address does not know is a warning, and the address still serves.

**Each deployment names only what it reaches elsewhere:**

```
service A (sales):     SINCPRO_CONTEXT_MAP="billing=grpc://billing-svc:50051"
service B (billing):   nothing — billing is local: its server, its workers, its crons, its startup
```

As a safety net, a context this process **serves** — `billing.serve(...)`, `open_host([...])`,
`open_host_routes([...])` — runs here even when its own map names it, so a service never calls
itself.

## The lifecycle

| Step | What happens |
|---|---|
| created | `UseFramework("billing")` reads this process's map — nothing named, local; named, a reference, said in the logs; registrations are kept, never resolved |
| decided | at every call: a **reference** when the map places it elsewhere and this process does not run it (`bus.is_reference`) |
| served | `serve`, `open_host`, `open_host_routes` mark it as run here (`run_here()`), then build it — the host's bus is real |
| built | `build_root_bus()` — a local bus builds once, under a lock; a reference returns at once, never fails, builds nothing |
| executed | `bus(dto)`, `get_async_bus()` — a reference forwards; a local bus executes, built on first use |

| The bus | `build_root_bus()` | `was_initialized` | `is_ready` |
|---|---|---|---|
| local | builds once — a second thread waits for that first build only | `True` | `True` |
| reference | returns at once | `False` | `True` |

Code that builds "just in case" — a catalog, a gateway, introspection, `with_trace` — works the same
on both, with no `if`.

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
2. Each DTO, dataclass or pydantic `Secret` is written as its values — dumped in pydantic's python
   mode, `bytes` included — and the name of its class, and `pickle`d into 1 MiB chunks; a large
   `bytes` value is handed over as itself and cut, never copied whole. The context's name, the DTO's
   registered name, the request context (each value packed the same way) and the trace
   (`traceparent`) ride as metadata or headers.
3. On the host the payload is read as it arrives, through an **allow-list** — builtin containers
   and scalars, `bytes`, `datetime`/`date`/`time`/`timedelta`, `Decimal`, `UUID`, enum members, and
   DTOs and secrets by the name of their class — and rebuilt by the DTO's own class, which fits the
   values: unknown fields ignored, defaults filled. It is executed inside the caller's request
   context and trace.
4. The answer is finished before its first chunk leaves, so a failure is answered as one — never as
   a cut stream — and it travels back the same way; the caller rebuilds it as the response class it
   asked for, else the one its handler declares, else each DTO as the class the host named — when
   the caller imported it; its values when not.

**Why not JSON:** pydantic writes `bytes` as UTF-8, so a PDF fails, and a base64 string cannot be
told from a text one on the way back. **Why `pickle` is safe here:** only values are packed — never
objects of the project — and the receiver refuses, while reading and before anything is built, any
class outside the allow-list; no code runs on receipt.

## Errors

| What happened | On the caller | Kind |
|---|---|---|
| the host raised a class the caller has imported | that class, with its `args` and attributes | its own |
| the host raised a class the caller never imported | `ContextFailed` — `.kind` names the host's class | `internal` |
| the DTO — or the answer — does not fit its class | `DTODoesNotFit` — `.fields` says which, and why | `invalid` |
| the host does not answer a DTO of that name (renamed or moved) | `UnknownDTOToExecute` | `internal` |
| nobody answering, or not hosting that context | `ContextUnavailable` — it did not run | `unavailable` |
| the host took the call and the connection was lost | `ContextOutcomeUnknown` — it may have run | `unknown_outcome` |
| the deadline passes | `ContextTimeout` (an `ContextOutcomeUnknown`) | `unknown_outcome` |

The caller never imports a module because the host named it: the class is looked up among what the
caller already imported, and rebuilt without calling its constructor. Dunder attributes
(`__notes__`, the framework's marks) stay on the host, as does an attribute that cannot be written;
details past 4 KiB travel as the message alone. The host still logs and reports its own failure.

`unknown_outcome` is not retryable as it is: verify — or retry with an idempotency key — before
doing it again or undoing it. Every wire answers it: REST 504, JSON-RPC `-32024`, gRPC
`DEADLINE_EXCEEDED`.

## What to know before relying on it

- **The same package on both ends, at whatever version each deployment has.** A DTO is found by its
  registered name — `module.qualname`, an event by its `name` — and rebuilt by the receiver's class.
  A field added with a default keeps every caller working; renaming or moving a class is the one
  change that breaks one, and it says so.
- **Python on both ends.** `/sincpro.Contexts/Execute` and `/sincpro/contexts/execute` carry Python's
  values; another language uses the gateway's `Struct` methods and its `.proto` export.
- **Values only.** Anything outside the allow-list raises `CannotTravel` on the receiving side; a
  context value that cannot be written (a lock, a connection) stays behind with a warning, and the
  call goes on.
- **Memory, not the wire, is the limit.** A payload is held once on each end; one larger than memory
  belongs in storage, with a reference in the DTO — the claim check pattern.
- **Opaque to generic tools.** The bodies are bytes: `grpcurl` and reflection see the method, not
  the fields.

## What it does not do

- **No retries.** A retried Command that already wrote would write twice; retrying is the caller's
  decision, or the infrastructure's — a mesh, Dapr.
- **No discovery or load balancing.** An address is an address; a Kubernetes Service, a gateway or a
  load balancer behind it does that.
- **No security of its own.** Who the call acts for crosses as credentials; the rest — TLS, mTLS, a
  gateway in front of the host, what goes in the context — is the project's.

## Reference

| | |
|---|---|
| `context_map` (conf file), `SINCPRO_CONTEXT_MAP` | where contexts are hosted, by name |
| `bus.hosted_by(address)`, `bus.hosted_at` | the same, in code |
| `bus.run_here()` | run here whatever the map says — what serving a context does |
| `bus.is_reference`, `bus.is_ready` | a reference to a context hosted elsewhere; ready to answer — built, or a reference |
| `bus.serve(address, attach)`, `serve_contexts([...], address, attach)`, `Attach`, `OpenHost` | the Open Host Service over gRPC |
| `open_host_routes([...])` | `sincpro_framework.remote_execution.entrypoint.http` — the Open Host Service on an ASGI app |
| `ContextFailed`, `ContextUnavailable`, `ContextOutcomeUnknown`, `ContextTimeout`, `DTODoesNotFit`, `CannotTravel` | `sincpro_framework.remote_execution` |
| `Wire`, `HostedAt`, `HostedContext`, `InvalidAddress` | `sincpro_framework.remote_execution` (from `transport.addresses`) |
| `open_host([...])` → `.server()` / `.mount(server)` | the gRPC open host as a server of its own (what `bus.serve` runs: the door and its health, never the contexts' public catalog), or mounted on a server of the caller's — beside a public `GrpcGateway` on one port, on purpose |

## Module map

`sincpro_framework.remote_execution`, laid out as `cron` and `migrations` are:

| Module | What it holds |
|---|---|
| `sincpro_framework.transport.addresses` | `Wire`, `HostedAt`, `HostedContext`, `InvalidAddress` — where a context is reached; the settings declare it too, so it imports neither |
| `domain/payload.py` | the published language: `pack` / `packed`, `unpack` / `unpacked`, `pack_context` / `unpack_context`, the allow-list, `ChunkReader` |
| `domain/errors.py` | what a failed call raises, an error's details, and raising a remote exception as itself |
| `adapters/transport.py` | `Transport`, the contract `transport_for` holds the transports by (only the adapters call it) |
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
