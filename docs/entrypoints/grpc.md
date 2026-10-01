# `entrypoint_grpc` — declared use cases as gRPC services

**`entrypoint_grpc`** publishes the use cases of one or more `UseFramework` instances as gRPC
services on Google's grpcio. Only what is **declared** is published — `@grpc()` on the handler, or
`gateway.bind(...)` — unless the composition asks for the whole catalog. Names follow Google's
AIPs: package `billing.v1`, service `BillingService`, method `IssueInvoice`. Every method is
**unary** and takes and returns `google.protobuf.Struct`. Discovery is **server reflection** (v1
and v1alpha) plus `sincpro.Introspection/Describe`. A failure answers a `google.rpc.Status` with
`ErrorInfo`; a call past its deadline never runs; past the concurrency limit a call is refused,
not queued; on SIGTERM health turns `NOT_SERVING` before the server drains.

The domain does not know gRPC exists. The Struct is the DTO's fields, by name.
`framework.context` / `with_trace` travel as call metadata, never as fields of the Command.

```
entrypoint_grpc                               Application                 Domain
───────────────                               ────────────                ──────
/billing.v1.BillingService/IssueInvoice       UseFramework × N            Feature
/billing.v1.BillingService/CancelInvoice      bus (access, idempotency,   ApplicationService
sincpro.Introspection/Describe                caching, observability)     DataTransferObject
server reflection, grpc.health.v1                                         ValueObject
Python remains: billing(dto)
```

Package: `sincpro_framework.entrypoints.grpc` (`GrpcGateway`, `GrpcWire`, `bus_call`,
`framework_interceptors`, `build_grpc_server`). Feature name: **`entrypoint_grpc`**.

What is exposed is decided once for every wire by the declared-exposure core
([PRD_14](../prd/PRD_14_declared-exposure.md)): `@internal`, `include` / `exclude`, `override` /
`bind`, the decorator, the group, then what gRPC derives. gRPC only adds its names and its host —
[PRD_15 §3](../prd/PRD_15_entrypoints-on-mature-hosts.md).

```bash
pip install sincpro-framework[grpc]
```

---

## What is published — declared by default

```python
from sincpro_framework.entrypoints.exposure import Exposure, GrpcBinding, grpc

@billing.feature(CommandIssueInvoice)
@auth.requires(BillingPermission.ISSUE)
@grpc()                                             # published on gRPC
class IssueInvoice(Feature): ...

@billing.feature(QueryInvoice)
@auth.public
@grpc(service="InvoiceReadService")                 # a second service in the same package
class GetInvoice(Feature): ...

GrpcGateway([billing])                              # IssueInvoice and Invoice — nothing else
GrpcGateway([billing]).bind(CommandCancelInvoice, GrpcBinding())   # one more, without a decorator
GrpcGateway([billing], exposure=Exposure.CATALOG)   # every use case, logged one by one
```

| Mode | Published |
|---|---|
| `Exposure.DECLARED` — **the default** | only use cases with a gRPC binding: `@grpc(...)` or `bind(Command, GrpcBinding(...))` |
| `Exposure.CATALOG` | every Feature and ApplicationService of every bus, each one logged at build — same names |

Whatever the mode, a use case marked `@internal` is on no wire, and the build refuses (every
reason at once, `ExposureRefused`) a surface that does not hold:

- a published use case on a bus guarded by `AccessControl` that declares neither
  `@auth.requires` nor `@auth.public`;
- a bus with no `AccessControl` at all, unless the gateway says `unguarded=True`;
- a name that is no proto identifier, or two use cases answering one path (below);
- a method on one of the framework's own services (`sincpro.Introspection`, health, reflection).

`gateway.surface()`, `gateway.manifest()` (per method: its path as `name`, the method, kind,
access, deprecation — snapshot it in a test) and `gateway.verify()` (the reasons, without raising)
are the core's, and work here as on every wire. `gateway.methods()` is the built table,
`{path: GrpcMethodSpec}`.

---

## Why Struct, and not a generated message per DTO

A typed `message ChargePayment { double amount = 1; }` needs **stable field numbers**, and a
Pydantic DTO has no such registry. Inserting a field in the middle of the class would renumber
everything after it, and every deployed client would keep reading the old number into the new
field — silently, because proto3 does not carry names on the wire.

Nothing in the bus can prevent that. So the wire keeps names: `Struct` is `map<string, Value>`,
which is exactly the compatibility contract `entrypoint_rpc` already has, and the one the DTO
itself has. Field-level types are still published — as the DTO's JSON Schema in `Describe`, the
same schema MCP publishes as a tool schema and OpenRPC as content descriptors.

There is no generated `*_pb2_grpc.py` anywhere in the repo, and no `protoc` at startup: services
are registered as generic handlers and described to reflection through a descriptor pool built
from the catalog when the server starts.

Two consequences worth knowing:

- Field names are **verbatim**. `card_number` stays `card_number`; no camelCase rewriting.
- A `Struct` has one number type, a double, in **both** directions. `amount: int` receives `5.0`
  and Pydantic coerces it back (a fractional value still fails validation); a `version: int` in
  the response reaches the client as `0.0`. Over JSON-RPC the same field stays an int. Clients
  that care about integers read the type from the `Describe` schema, not from the value.
- A `Struct` has no bytes type, so `GrpcWire` declares `carries_bytes = False`: a Command with a
  `bytes` field gets no method, and `@grpc()` on it is refused. This is the gateway's design, not
  gRPC's. Between Sincpro Python services, `remote_execution` (see
  [bounded-contexts-across-services.md](bounded-contexts-across-services.md)) carries `bytes`
  natively over one `stream_stream` call.

---

## What comes back: the response shape

The bus discards `execute(dto, return_type)`'s `return_type` at runtime — it is a typing hint for
the caller. So the catalog reads the **declaration** instead: `execute`'s return annotation, or
the second parameter of `Feature[Command, Response, Ctx]` when the method is unannotated.

Every shape the bus admits is published and travels:

| `execute` declares | `Describe` `result` | On the wire |
|---|---|---|
| `-> ResponseDTO` | its JSON Schema | the DTO's fields |
| `-> MappedContact` (a dataclass) | its JSON Schema | the dataclass's fields |
| `-> Invoice` (an `Entity` — also a dataclass) | its JSON Schema | fields, `datetime` as ISO-8601, recorded events left behind |
| `-> list[ResponseDTO]` | an `array` schema | `{"result": [ ... ]}` |
| `-> dict` | an open object | the dict |
| nothing declared | `{"type": "object"}` | still executes; only the promise is missing |

A leaf Pydantic cannot render — a Zeep SOAP response parked on an `Any` field — is stringified
and warned about, naming the offending type. The Feature already ran; the host does not crash
over the answer.

Declaring the return type is what makes the shape appear in `Describe`, in the OpenRPC
`result`, and as the `// Struct: the fields of X` comment in the exported `.proto`. An
unannotated `execute` costs nothing at runtime and publishes nothing.

---

## A Command that is a dataclass

`TypeDTO` admits `DataTransferObject | DataclassInstance`, so a context that maps its domain
imperatively can register a dataclass Command. The entrypoints follow: the schema comes from a
Pydantic `TypeAdapter`, validation raises the same `ValidationError` (→ `INVALID_ARGUMENT`), and
the MCP tool signature is built from `dataclasses.fields` instead of `model_fields`.

A `DataTransferObject` is still the default — it carries Value Objects, `Field` descriptions and
`use_attribute_docstrings`, none of which a plain dataclass has to give the wire.

---

## Names — AIP, no layer

```text
/{package}.{Service}/{Method}          /billing.v1.BillingService/IssueInvoice
```

| Element | Rule | Changed by |
|---|---|---|
| package | `{alias}.v{major}` — `v1` by default | `gateway.group(billing, version="v2")` → `billing.v2`; `group(billing, package="acme.billing")` → `acme.billing.v1`; a package already ending in `.vN` is taken as it is |
| service | `{Alias}Service`, the alias in PascalCase — `sales_orders` → `SalesOrdersService`; one per context | `@grpc(service="InvoiceReadService")` splits a context into several services of its package |
| method | the DTO's name without `Command` / `Query`, PascalCase — `CommandIssueInvoice` → `IssueInvoice`, `QueryInvoice` → `Invoice` | `@grpc(method="Void")`, or `gateway.override(Command, method=...)` |
| fields | verbatim snake_case — the Struct's keys | — |

The layer is **not** in any name: a Feature and an ApplicationService of one context are methods
of the same service, so promoting one to the other renames nothing a client generated. The
`/{alias}.Features/{Dto}` and `/{alias}.AppServices/{Dto}` names of before are **removed**, with
no transition: a client dialing them gets `UNIMPLEMENTED`.

**Two use cases answering one path fail the build.** `CommandInvoice` and `QueryInvoice` in one
service both strip to `Invoice`; the gateway refuses, naming both — give one of them
`@grpc(method=...)`. The same holds for two contexts put in one package by their groups.

The alias is the composition-root key (`{"billing": billing}`) or, for a bus added by itself, its
name made a proto identifier (`sincpro-billing` → `sincpro_billing`). It becomes the first segment
of the package, so it allows no hyphen and no dot — `bank-account` is refused at `add()` with the
reason. DTO names collide across bounded contexts; the package separates them.

A project that names differently subclasses `GrpcWire` (its `derive`, `validate`, `build`) and
hands it to `GrpcGateway(port=MyWire())`.

```python
GrpcGateway({"billing": billing, "sales": sales}).run("0.0.0.0:50051")
```

```bash
grpcurl -plaintext localhost:50051 list
grpcurl -plaintext -d '{"total": 50}' localhost:50051 billing.v1.BillingService/IssueInvoice
```

`layers=("app_services",)` still narrows the published use cases by layer, and `include` /
`exclude` / `wrap` per instance work as they do on every entrypoint.

---

## Context and tracing

gRPC has no sibling of `params` the way a JSON-RPC request has `context`, so the transport
carries it and the payload stays DTO fields only.

| Metadata | Framework |
|---|---|
| `x-correlation-id` | `correlation_id` in `framework.context` |
| `traceparent` / `tracestate` | `carrier` → `framework.with_trace(...)` for OTel parent adoption |
| `sp-ctx-<key>` | `context["<key>"]` — tenant, user id, whatever the bus reads |
| the call's deadline (`grpc-timeout`) | `context["deadline"]` — **epoch seconds**, absent when the caller set none |

### Deadlines

The caller's deadline enters the request context as an absolute time, so an adapter the Feature
calls can bound its own timeout by it (`context["deadline"] - time.time()`) however long the
request travelled first. A call whose deadline **already passed** when the handler is reached —
held by a slow interceptor, a token introspection, a cold connection — answers
`DEADLINE_EXCEEDED` and **never runs**: its caller gave up, and a charge nobody waits for is a
charge nobody sees. (grpcio itself drops a call that expires while still queued for a worker.)

```python
from sincpro_framework.entrypoints.grpc.client import GrpcClient

with GrpcClient("localhost:50051") as client:
    client.call(
        "/qr.v1.QrService/CheckQRStatusEconomico",
        {"transaction_id": "t-1"},
        context={"correlation_id": "req-9", "tenant": "acme"},
    )
```

Interceptors, error handlers and bus tracing are unchanged.

---

## Errors

A failure is answered with `context.abort_with_status(...)`: the status code, a message, and a
[`google.rpc.Status`](https://google.aip.dev/193) in `grpc-status-details-bin` that every
generated client (Go, Java, TypeScript, `grpcio-status` in Python) decodes. Its kind is the one
classification every wire shares ([`transport/failures.py`](../../sincpro_framework/transport/failures.py),
PRD_15 §1.3); the code is gRPC's own:

| Kind | Raised by | gRPC status | Details besides `ErrorInfo` |
|---|---|---|---|
| — | an unknown method | `UNIMPLEMENTED` (the transport's own answer) | — |
| invalid | the DTO, Pydantic, a Value Object | `INVALID_ARGUMENT`; the message is the Pydantic errors as JSON | `BadRequest.field_violations`, one per field |
| unauthenticated | `Unauthenticated` | `UNAUTHENTICATED` | trailers `www-authenticate` and `sp-auth-refusal` |
| permission denied | `PermissionDenied` | `PERMISSION_DENIED` | trailer `sp-auth-refusal` |
| not found | an error declaring `failure_kind = FailureKind.NOT_FOUND` | `NOT_FOUND` | — |
| conflict | `StaleAggregate` / `DuplicateAggregate` | `ABORTED` (read again, decide again) / `ALREADY_EXISTS` | — |
| in progress | `AlreadyInProgress` (idempotency) | `ABORTED` | `RetryInfo` |
| key reused | `KeyReused` (idempotency) | `FAILED_PRECONDITION` | `PreconditionFailure` |
| domain | any other `DomainError` | `FAILED_PRECONDITION`, with its message — it was written for the caller | `PreconditionFailure` |
| exhausted | an error declaring `FailureKind.EXHAUSTED` | `RESOURCE_EXHAUSTED` | `RetryInfo` |
| unavailable | an error declaring `FailureKind.UNAVAILABLE` | `UNAVAILABLE` | `RetryInfo` |
| internal | anything else | `INTERNAL`, `"Internal error"`, **and nothing else** | — |

Not every `DomainError` is `FAILED_PRECONDITION`: a conflict is `ABORTED` or `ALREADY_EXISTS`, an
idempotency run in flight is `ABORTED`.

**`ErrorInfo` is always there.** `reason` is the stable UPPER_SNAKE a client switches on — the
error's class when the caller may read the error (`InvoiceNotFound` → `INVOICE_NOT_FOUND`,
`ContractViolation` → `CONTRACT_VIOLATION`), the kind otherwise (`INVALID`, `INTERNAL`,
`UNAVAILABLE`): the class of an inside failure (`OperationalError`) says what runs inside.
`domain` is the served package (`billing.v1`), `metadata["kind"]` the kind; an auth refusal adds
its `reason`, `requirement` or `step_up`.

`PreconditionFailure.violations[].subject` is the Command's name (`CommandIssueInvoice`).

**`RetryInfo`** comes with the kinds a caller retries as they are — in progress, exhausted,
unavailable: one second, or what the error says in a `retry_after` attribute (seconds or a
`timedelta`). A conflict carries none: retrying the same write is wrong, reading again is right.

**An error declares a kind the shared ones do not name** with a class attribute, without the
transport importing it:

```python
class InvoiceNotFound(DomainError):
    failure_kind = FailureKind.NOT_FOUND         # NOT_FOUND, reason INVOICE_NOT_FOUND

class LedgerDown(Exception):                     # not a DomainError: its text is never told
    failure_kind = FailureKind.UNAVAILABLE       # UNAVAILABLE + RetryInfo, reason UNAVAILABLE
```

**`INTERNAL` never carries the exception's text** — not in the message, not in a detail, not as
the reason. It carries connection strings, statements and paths; it goes to the log. The
disclosure rule is one function every wire shares (`said_to_the_caller`); only the encoding is
per protocol.

The refined kinds (`not found`, `in progress`, `key reused`, `exhausted`, `unavailable`) come from
`refined_failure_kind`. `failure_kind` — what the other wires' tables are keyed by — keeps
answering the kinds they already encode (an idempotency refusal is still `domain` there) until
each wire maps the new ones.

Binary DTOs (`bytes`) are skipped at catalog time, same as MCP and JSON-RPC — a `Struct` has no
bytes value. They stay callable in-process via `framework(dto)`.

---

## Exporting the `.proto`

Reflection is enough for grpcurl, Postman and any client that can read it at runtime. A Go or
TypeScript team generating stubs at build time wants a file:

```python
GrpcGateway([billing]).write_proto_files("build/proto")   # build/proto/billing/v1/billing.proto
```

```proto
// Generated by sincpro-framework from the bus catalog. Do not edit by hand.
syntax = "proto3";

package billing.v1;

import "google/protobuf/struct.proto";

service BillingService {
  // Issue an invoice.
  // Struct: the fields of Issued. Full schema in Describe.
  rpc IssueInvoice(google.protobuf.Struct) returns (google.protobuf.Struct);
}
```

One file per package, laid out as buf expects (`billing/v1/billing.proto`), plus `sincpro.proto`
for the introspection service. A binding declared `deprecated` renders
`{ option deprecated = true; }` and is marked deprecated in reflection too. The rendering lives in
[`grpc/proto.py`](../../sincpro_framework/entrypoints/grpc/proto.py) and the names in
[`grpc/naming.py`](../../sincpro_framework/entrypoints/grpc/naming.py) — neither imports `grpc`
nor `protobuf`, so a build step can export the contract without the `[grpc]` extra.

---

## Health, and graceful shutdown — on by default

`grpc.health.v1.Health` is served by every `.server()`/`.run()`/`.handlers()`/`.mount()` unless
`grpcio-health-checking` is missing (a warning, never a failed start). It answers **per service**
— each served service by its full name (`billing.v1.BillingService`), `sincpro.Introspection`,
and `""` for the whole server; any other name is `NOT_FOUND`. `Check` and `Watch` re-evaluate
readiness on every call — the default asks whether every published `UseFramework` is still built:

```bash
grpcurl -plaintext -d '{"service": "billing.v1.BillingService"}' localhost:50051 grpc.health.v1.Health/Check
grpc_health_probe -addr=localhost:50051   # what Kubernetes' native gRPC probe speaks
```

A deeper probe (a DB ping, a queue connection) replaces the default:

```python
GrpcGateway([billing]).run(health_check=lambda: db.ping() and queue.connected())
```

`.run()` hooks `SIGTERM`/`SIGINT` by default. On the signal, **every service turns
`NOT_SERVING` first** — whatever the health check says — and then `server.stop(grace)` drains
in-flight calls for up to `grace` seconds (5.0 by default). A caller that owns its own lifecycle
does the same with `gateway.drain()` before its own stop. `handle_signals=False` opts out of the
hook; signals can only be hooked from the main thread — calling `run()` off it logs once and
serves with no hook rather than raising.

```python
GrpcGateway([billing]).run(grace=10.0)                  # more time to drain
GrpcGateway([billing]).run(handle_signals=False)         # caller owns the signal
```

`stop(grace)` refuses new calls at once, so the flip is what a probe or a `Watch` sees between the
signal and the refusal; a deployment that wants its load balancer to stop routing before the
refusal gives the pod a `preStop` delay.

### Keepalive, connection age, message size

`.server()` / `.run()` set these channel options by default (`wire.server_options`); any of them
is replaced by the caller's `options=` entry of the same key:

| Option | Default | Why |
|---|---|---|
| `max_message_bytes=` → `grpc.max_receive_message_length` / `grpc.max_send_message_length` | **4 MiB**, both ways | grpcio's own receive limit, made explicit; a message over it is refused `RESOURCE_EXHAUSTED` before the use case runs |
| `max_connection_age=` → `grpc.max_connection_age_ms` | **300 s** (+ 30 s grace) | clients reconnect periodically, so a rolling deploy or a new replica gets its share instead of every client pinned to the old pods; `None` keeps connections for ever |
| `grpc.keepalive_time_ms` / `grpc.keepalive_timeout_ms` | 60 s / 20 s | the server pings an idle connection, and a peer a load balancer dropped silently is noticed |
| `grpc.keepalive_permit_without_calls` / `grpc.http2.min_recv_ping_interval_without_data_ms` | 1 / 10 s | clients may keep idle connections alive with their own pings, every 10 s at most |

```python
GrpcGateway([billing]).server(max_message_bytes=16 * 1024 * 1024)       # 16 MiB both ways
GrpcGateway([billing]).server(options=[("grpc.keepalive_time_ms", 30_000)])
```

### Backpressure

A server admits at most `maximum_concurrent_rpcs` calls at once — **2 × `max_workers`** by
default: one running per worker and one queued behind each. Past it the call is answered
`RESOURCE_EXHAUSTED` at once, a status a client backs off on, instead of queueing silently behind
the pool until every caller's own deadline fires. Health and reflection calls count too.

```python
GrpcGateway({"qr": qr}).run(max_workers=16)                           # 32 admitted
GrpcGateway({"qr": qr}).server(max_workers=16, maximum_concurrent_rpcs=64)
```

The limit lives in `transport.grpc.server(...)`, so remote execution's host (`bus.serve(...)`)
has the same default.

---

## Composability, and full control

Three ways to use the wire (PRD_15 §1.1), every one of them calling the bus:

| Mode | You write | You get |
|---|---|---|
| auto | `GrpcGateway([billing]).run(...)` | the server, every declared method, reflection, health, drain |
| mix | `.handlers()` on a server of yours, `interceptors=` on `.server()`, a `group` / `override` / `bind` | the same methods on your server and lifecycle |
| full control | a servicer of your own, on a server of yours | `bus_call`, `framework_interceptors()`, `.mount(server, reflection_extra=[...])` |

**`.handlers()`** is a tuple of `grpc.GenericRpcHandler` — the served services, Introspection and
health — with no server and no reflection around them. **`.mount(server, reflection_extra=())`**
adds the handlers **and** one server reflection over the gateway's services and the hand-written
services in `reflection_extra`, to a server you built.

### A hand-written servicer

```python
from sincpro_framework.entrypoints.grpc import GrpcGateway, bus_call, framework_interceptors

class BillingServicer(billing_pb2_grpc.BillingServiceServicer):
    def IssueInvoice(self, request, context):
        return bus_call(
            context, billing,
            lambda: CommandIssueInvoice(customer_id=request.customer_id),
            to_response=lambda issued: billing_pb2.IssueInvoiceResponse(number=issued.number),
        )

server = grpc.server(futures.ThreadPoolExecutor(8), interceptors=framework_interceptors())
billing_pb2_grpc.add_BillingServiceServicer_to_server(BillingServicer(), server)
GrpcGateway([billing]).mount(server, reflection_extra=[billing_pb2.DESCRIPTOR])
```

`bus_call(context, bus, dto, to_response=None, *, domain=None)` runs **the same code path** the
generated methods run — a test holds the two to the same status, details, trailers and span:

1. the call's metadata into the request context (`sp-ctx-*`, correlation id, `traceparent`),
2. the caller's credentials (metadata, the mTLS certificate), authenticated in the same thread
   the bus runs in,
3. the deadline pre-check — `DEADLINE_EXCEEDED` without running,
4. `bus(dto)` — the access guard, idempotency, caching, the Feature's span and Sentry apply by
   construction,
5. a failure answered as the same `google.rpc.Status`, `domain` the bus's default package
   (`billing.v1`, or `domain=`).

`dto` is the Command or a function building it: built inside, a validation error is answered
`INVALID_ARGUMENT` with `BadRequest`, as the generated path answers it. `to_response` turns the
bus's answer into your message; without it the answer is a `Struct`.

`framework_interceptors()` is the deadline pre-check and a debug call log, for servicers that
never reach `bus_call`. **Never auth**: the bus's `AccessControl` is the only guard, and a
servicer that calls the bus is guarded.

**A hand-written method path the gateway also serves fails the build**: `mount` raises
`ExposureRefused` naming the path and the Command before mounting anything — which of the two
answered would otherwise depend on the order handlers were added. Exclude the use case from the
gateway (`exclude=`, or no `@grpc()`), or rename the hand-written method.

### Per-bus policy via interceptor

gRPC has no preflight — every call carries its full method path and metadata when an interceptor
sees it, so one interceptor branching on the package reaches every context differently. This is
for transport policy (an internal network token, rate limits), never for authorization, which
stays in the bus:

```python
class PerBusPolicy(grpc.ServerInterceptor):
    def intercept_service(self, continuation, handler_call_details):
        alias = handler_call_details.method.lstrip("/").split(".", 1)[0]
        if alias == "internal" and not _has_valid_token(handler_call_details):
            def deny(_request, context):
                context.abort(grpc.StatusCode.PERMISSION_DENIED, f"bus [{alias}] locked")
            return grpc.unary_unary_rpc_method_handler(deny)
        return continuation(handler_call_details)
```

### Server reflection — v1 and v1alpha

Both `grpc.reflection.v1.ServerReflection` and `grpc.reflection.v1alpha.ServerReflection` are
served: current `grpcurl` and Postman ask v1 first, older clients v1alpha. `grpcio-reflection`
ships v1alpha only; the two are the same messages field for field, so one servicer over one
private descriptor pool answers both — the gateway's packages, Introspection, the hand-written
files of `reflection_extra` (and the files they import), and each reflection service itself.

---

## Public API

| Symbol | Role |
|---|---|
| `GrpcGateway(buses, exposure=Exposure.DECLARED, unguarded=False, port=None)` | Composition root. Several contexts, one server. |
| `.add(alias, framework, include=, exclude=, wrap=)` | Fluent mount. |
| `.group(bus, package=, version=)` / `.override(Command, service=, method=)` / `.bind(Command, GrpcBinding(...))` | Shape and publish (PRD_14). |
| `.surface()` / `.manifest()` / `.verify()` | The validated surface, its inventory, the reasons it would be refused. |
| `.methods()` | `{path: GrpcMethodSpec}` — what is served. |
| `.describe()` | The document `sincpro.Introspection/Describe` answers. |
| `.proto_files()` / `.write_proto_files(dir)` | `.proto` export, `billing/v1/billing.proto` per package. |
| `.handlers(health_check=)` | The generic handlers (health included), for a server of yours. |
| `.mount(server, reflection_extra=(), reflection=True, health_check=)` | Handlers and one reflection over generated and hand-written services; refuses a clashing hand-written path. |
| `.server(max_workers=, interceptors=, options=, reflection=, health_check=, maximum_concurrent_rpcs=, max_message_bytes=, max_connection_age=)` | A `grpc.Server`, no port bound, not started. |
| `.run(address=, credentials=, grace=, handle_signals=, ...)` | Bind, serve; on SIGTERM/SIGINT health `NOT_SERVING`, then drain. |
| `.drain()` | Every service `NOT_SERVING` in health, for a caller owning its lifecycle. |
| `bus_call(context, bus, dto, to_response=None, *, domain=None)` | A hand-written servicer's call, on the generated path. |
| `framework_interceptors()` | Deadline pre-check and call log for a server of yours. |
| `GrpcWire` | The naming `Wire` — subclass it to name differently (`port=`). |
| `build_grpc_server(instances, exposure=, unguarded=, **server_kwargs)` | Same as `GrpcGateway(...).server()`. |
| `GrpcClient(target)` | Python caller with no generated stubs: dict in, dict out. |
| — | This gateway never answers `/sincpro.Contexts/Execute`: that is remote execution's internal door ([calling services](bounded-contexts-across-services.md)), served by `bus.serve(...)`, or mounted on this gateway's server on purpose with `open_host([...]).mount(gateway.server())`. |

`credentials` is a `grpc.ServerCredentials`; without it the port is insecure, which is for
localhost and for a mesh that terminates TLS in front of the process — never for a port exposed
as it is. Authentication is the bus's `AccessControl`, fed by the call's metadata and client
certificate.

---

## Module map

```
sincpro_framework/
├── introspection/         # shared: bus → FeatureOrAppServiceMetadata/DtoMetadata
├── transport/             # shared with remote execution, exposing nothing
│   ├── failures.py        # the kinds, the stable reason, retry delay, what a caller may read
│   └── grpc.py            # server (pool, backpressure), health, metadata ↔ context, deadline
└── entrypoints/
    ├── scalar_executor.py # shared: Scalar (dict) in/out execution against a UseFramework
    ├── catalog.py         # shared: metadata → PackedFeatureOrAppService (JSON schema, binary check)
    ├── gateway.py         # shared: Gateway — declared exposure, precedence, build validation
    ├── exposure/          # shared: the bindings and decorators (@grpc, ...) — PRD_14
    └── grpc/              # entrypoint_grpc
        ├── __init__.py    # GrpcGateway, GrpcWire, bus_call, framework_interceptors, build_grpc_server
        ├── naming.py      # AIP names + GrpcWire (derive, validate, build) — no grpc import
        ├── proto.py       # GrpcMethodSpec, Describe document, .proto rendering — no grpc import
        ├── wire.py        # Struct, the one call path, bus_call, rich status, health, reflection, options
        ├── entrypoint.py  # the GrpcGateway facade: surface → server, mount, run, drain
        └── client.py      # dict-in/dict-out client for Python callers
```

`entrypoint.py` orchestrates, `wire.py` holds everything that needs grpcio; `naming.py` and
`proto.py` exist so the names and the contract are computed and exported without the runtime.

---

## Constraints

| Rule | Why |
|---|---|
| No gRPC types on Feature | Hexagonal. |
| Unary only | The bus answers one DTO with one response; streaming would be a second execution model. |
| Alias is a proto package segment | No hyphen, no dot. |
| No layer in a name | A Feature promoted to an ApplicationService keeps its method. |
| Declared by default | Nothing is public by being registered. |
| Payload is `Struct` | Field numbers cannot be derived safely from a Pydantic class. |
| A thread pool, not `grpc.aio` | `execute` blocks, and `framework.context` is a ContextVar the handler enters in its own worker thread. |
| `grpcio` stays an extra | Core bus installs without a gRPC stack. `[grpc]` brings `grpcio`, `grpcio-reflection`, `grpcio-health-checking`, `grpcio-status` and `protobuf`. |
