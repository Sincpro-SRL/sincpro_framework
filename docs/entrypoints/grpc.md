# `entrypoint_grpc` — bus catalog as gRPC services

**`entrypoint_grpc`** publishes one or more `UseFramework` instances as gRPC services. Every
Feature and ApplicationService is one **unary** method taking and returning
`google.protobuf.Struct`. Discovery is **server reflection** plus
`sincpro.Introspection/Describe`.

The domain does not know gRPC exists. The Struct is the DTO's fields, by name.
`framework.context` / `with_trace` travel as call metadata, never as fields of the Command.

```
entrypoint_grpc                   Application                 Domain
───────────────                   ────────────                ──────
/qr.Features/ChargePayment        UseFramework × N            Feature
/qr.AppServices/…                 FeatureBus                  ApplicationService
sincpro.Introspection/Describe    registries                  DataTransferObject
server reflection                                             ValueObject
Python remains: framework(dto)
```

Package: `sincpro_framework.entrypoints.grpc` (`GrpcGateway`, `build_grpc_server`). Feature name:
**`entrypoint_grpc`**.

The bus projection is shared: `sincpro_framework.introspection` describes what exists,
`Catalog` / `PackedFeatureOrAppService` in [`catalog.py`](../../sincpro_framework/entrypoints/catalog.py)
packages it for JSON. gRPC only adds a wire — the same one JSON-RPC and MCP add theirs to.

```bash
pip install sincpro-framework[grpc]
```

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

## Method paths

```text
/{alias}.{Layer}/{DtoName}
```

| Layer | Bus registry | Service |
|---|---|---|
| `features` | Feature | `{alias}.Features` |
| `app_services` | ApplicationService | `{alias}.AppServices` |

Alias is the composition-root key (`qr`, `cybersource`, `bank_account`) and becomes the **proto
package**, so unlike a JSON-RPC alias it allows no hyphen — `bank-account` is refused at `add()`
with the reason. DTO names collide across bounded contexts; the package is what separates them.

```python
from sincpro_framework.entrypoints.grpc import GrpcGateway
from sincpro_payments_sdk.apps.qr import qr
from sincpro_payments_sdk.apps.cybersource import cybersource

GrpcGateway({"qr": qr, "cybersource": cybersource}).run("0.0.0.0:50051")
```

```bash
grpcurl -plaintext localhost:50051 list
grpcurl -plaintext -d '{"amount": 50.0}' localhost:50051 qr.Features/CommandCreateQREconomico
```

Default: both layers. Filter with `layers=("app_services",)` when you want a smaller surface;
`include` / `exclude` / `wrap` per instance work as they do on every entrypoint.

---

## Context and tracing

gRPC has no sibling of `params` the way a JSON-RPC request has `context`, so the transport
carries it and the payload stays DTO fields only.

| Metadata | Framework |
|---|---|
| `x-correlation-id` | `correlation_id` in `framework.context` |
| `traceparent` / `tracestate` | `carrier` → `framework.with_trace(...)` for OTel parent adoption |
| `sp-ctx-<key>` | `context["<key>"]` — tenant, user id, whatever the bus reads |

```python
from sincpro_framework.entrypoints.grpc.client import GrpcClient

with GrpcClient("localhost:50051") as client:
    client.call(
        "/qr.Features/CommandCheckQRStatusEconomico",
        {"transaction_id": "t-1"},
        context={"correlation_id": "req-9", "tenant": "acme"},
    )
```

Interceptors, error handlers and bus tracing are unchanged.

---

## Errors

| Level | gRPC status |
|---|---|
| Unknown method | `UNIMPLEMENTED` (the transport's own answer) |
| DTO / Pydantic / Value Object | `INVALID_ARGUMENT`, details are the Pydantic errors as JSON |
| Feature raised a `DomainError` | `FAILED_PRECONDITION`, with its message — it was written for the caller |
| Feature raised anything else | `INTERNAL`, `"Internal error"`, **and nothing else**. The exception's own text carries connection strings, statements and paths; it goes to the log, not over the wire |

The disclosure rule is one function shared with JSON-RPC:
[`entrypoints/errors.py`](../../sincpro_framework/entrypoints/errors.py). Only the status code is
per-protocol.

Binary DTOs (`bytes`) are skipped at catalog time, same as MCP and JSON-RPC — a `Struct` has no
bytes value. They stay callable in-process via `framework(dto)`.

---

## Exporting the `.proto`

Reflection is enough for grpcurl, Postman and any client that can read it at runtime. A Go or
TypeScript team generating stubs at build time wants a file:

```python
GrpcGateway({"qr": qr}).write_proto_files("build/proto")
```

```proto
// Generated by sincpro-framework from the bus catalog. Do not edit by hand.
syntax = "proto3";

package qr;

import "google/protobuf/struct.proto";

// features registered on UseFramework instance [payment-qr].
service Features {
  // Create a QR for the Economico gateway.
  // Struct: the fields of ResponseCreateQREconomico. Full schema in Describe.
  rpc CommandCreateQREconomico(google.protobuf.Struct) returns (google.protobuf.Struct);
}
```

One file per alias, plus `sincpro.proto` for the introspection service. The rendering lives in
[`grpc/proto.py`](../../sincpro_framework/entrypoints/grpc/proto.py), which imports neither
`grpc` nor `protobuf` — a build step can export the contract without the `[grpc]` extra
installed.

---

## Health, and graceful shutdown — on by default

`grpc.health.v1.Health` is served by every `.server()`/`.run()`/`.handlers()` unless
`grpcio-health-checking` is missing (a warning, never a failed start). `Check` and `Watch`
re-evaluate readiness on every call — the default asks whether every registered
`UseFramework` is still built, the same thing `Catalog` already forces at construction:

```bash
grpcurl -plaintext localhost:50051 grpc.health.v1.Health/Check
grpc_health_probe -addr=localhost:50051   # what Kubernetes' native gRPC probe speaks
```

A deeper probe (a DB ping, a queue connection) replaces the default:

```python
GrpcGateway({"qr": qr}).run(health_check=lambda: db.ping() and queue.connected())
```

`.run()` also hooks `SIGTERM`/`SIGINT` by default: `server.stop(grace)` drains in-flight calls
for up to `grace` seconds (5.0 by default) instead of a rolling restart killing them mid-request.
`handle_signals=False` opts out for a caller hooking its own process lifecycle (a supervisor
that sends its own stop sequence); signals can only be hooked from the main thread — calling
`run()` off it logs once and serves with no hook rather than raising.

```python
GrpcGateway({"qr": qr}).run(grace=10.0)                  # more time to drain
GrpcGateway({"qr": qr}).run(handle_signals=False)         # caller owns the signal
```

---

## Composability: handlers as data, policy via interceptor

`entrypoint_grpc` never decides auth, TLS, health checks or the server's own lifecycle — it
hands the host what it needs to build its own `grpc.Server`:

- **`.handlers()`** — a tuple of `grpc.GenericRpcHandler`, no `grpc.Server` around them. Mount
  several gateways' services on one port, plug in `grpc_health.v1.health` yourself, swap the
  threaded executor for `grpc.aio.server()` — this package never constructs the server for you
  when you take this path.
- **`.server(interceptors=, options=, ...)`** — the convenience path, for a process that serves
  only this gateway, still composable through `grpc.ServerInterceptor`.

```python
server = grpc.server(futures.ThreadPoolExecutor(max_workers=8))
server.add_generic_rpc_handlers(GrpcGateway({"qr": qr}).handlers())
# ... the host's own health service, its own credentials, its own port ...
```

**Per-bus policy is the interceptor's job, and it already has what it needs.** Unlike CORS,
gRPC has no preflight — every call already carries its full method path and metadata when an
interceptor sees it, so one interceptor branching on the path reaches every bus differently:

```python
class PerBusPolicy(grpc.ServerInterceptor):
    def intercept_service(self, continuation, handler_call_details):
        alias = handler_call_details.method.lstrip("/").split(".", 1)[0]
        if alias == "internal" and not _has_valid_token(handler_call_details):
            def deny(_request, context):
                context.abort(grpc.StatusCode.PERMISSION_DENIED, f"bus [{alias}] locked")
            return grpc.unary_unary_rpc_method_handler(deny)
        return continuation(handler_call_details)

GrpcGateway({"public": public, "internal": internal}).server(
    interceptors=[PerBusPolicy()],
)
```

`public.Features/*` passes through untouched; `internal.Features/*` is rejected unless the
caller's metadata carries the token — one interceptor, two policies, nothing in `entrypoint_grpc`
knows what "auth" means. Reflection composes the same way: `wire.enable_reflection(server,
gateway.methods())` if a hand-built server still wants it.

---

## Public API

| Symbol | Role |
|---|---|
| `GrpcGateway({"qr": qr, ...})` | Composition root. Several instances, one process. |
| `.add(alias, framework, include=, exclude=, wrap=)` | Fluent mount. |
| `.methods()` | `{path: GrpcMethodSpec}` — what is served. |
| `.describe()` | The catalog `sincpro.Introspection/Describe` answers. |
| `.proto_files()` / `.write_proto_files(dir)` | `.proto` export for client stub generation. |
| `.handlers(health_check=)` | This gateway's `GenericRpcHandler`s (health included), for a host's own `grpc.Server`. |
| `.server(max_workers=, interceptors=, options=, reflection=, health_check=)` | A `grpc.Server` with no port bound and not started. |
| `.run(address=, credentials=, grace=, handle_signals=, ...)` | Bind, serve, drain on SIGTERM/SIGINT. |
| `build_grpc_server(instances)` | Same as `GrpcGateway(...).server()`. |
| `GrpcClient(target)` | Python caller with no generated stubs: dict in, dict out. |

`credentials` is a `grpc.ServerCredentials`; without it the port is insecure, which is for
localhost and for a mesh that terminates TLS in front of the process — never for a port exposed
as it is. Authentication is a `grpc.ServerInterceptor` passed to `interceptors`, or a
`Catalog.wrap` around one DTO's `run`.

---

## Module map

```
sincpro_framework/
├── introspection/         # shared: bus → FeatureOrAppServiceMetadata/DtoMetadata
└── entrypoints/
    ├── scalar_executor.py # shared: Scalar (dict) in/out execution against a UseFramework
    ├── catalog.py         # shared: metadata → PackedFeatureOrAppService (JSON schema, binary check)
    ├── errors.py          # shared: what a caller may read of a failure
    └── grpc/              # entrypoint_grpc
        ├── __init__.py    # re-exports GrpcGateway, build_grpc_server
        ├── proto.py       # names, paths, Describe document, .proto rendering — no grpc import
        ├── wire.py        # Struct marshalling, handlers, status mapping, reflection pool
        ├── entrypoint.py  # orchestrates catalog.py + proto.py + wire.py — the GrpcGateway facade
        └── client.py      # dict-in/dict-out client for Python callers
```

Same recipe as `entrypoint_rpc`: `entrypoint.py` orchestrates, the wire module holds everything
protocol-specific. The extra file here is `proto.py`, which exists so the contract can be
exported without the runtime.

---

## Constraints

| Rule | Why |
|---|---|
| No gRPC types on Feature | Hexagonal. |
| Unary only | The bus answers one DTO with one response; streaming would be a second execution model. |
| Alias is a proto package | No hyphen, no dot. |
| Payload is `Struct` | Field numbers cannot be derived safely from a Pydantic class. |
| A thread pool, not `grpc.aio` | `execute` blocks, and `framework.context` is a ContextVar the handler enters in its own worker thread. |
| `grpcio` stays an extra | Core bus installs without a gRPC stack. |
