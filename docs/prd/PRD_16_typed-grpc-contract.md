# PRD_16: The typed gRPC contract — built at runtime, exported as a static contract

- **Status**: specified, not built. Details PRD_15 §3.1 (`typed`, phase 2) and §3.3 (streaming,
  phase 3). A proof of concept validated §1–§7 (see [Evidence](#evidence)).
- **Depends on**: PRD_14 (declared exposure, `Wire` port), PRD_15 §3 (`GrpcGateway`, AIP naming,
  `google.rpc.Status`, deadlines, reflection), `transport.failures`, `remote_execution` (kept apart,
  §10), PRD_06/PRD_07 (runtime use cases, for §5.4).
- **Philosophy**: the DTO stays the source of truth. The framework builds the protobuf contract
  from it **at runtime** — descriptors, message classes, handlers, reflection — with no `protoc`
  in the service, and **exports the same contract as static files** (`.proto` + a lock) for
  anyone who compiles clients, the way a REST service serves `/openapi.json` and also commits
  `openapi.json`. One derivation, two outputs, never two sources.

## Problem

`GrpcGateway` serves every method as `google.protobuf.Struct` in, `Struct` out, unary only:

- **No `bytes`**: a `Struct` has no bytes type, so `GrpcWire.carries_bytes = False` and a Command
  with a file has no method (`@grpc()` on it is refused).
- **Numbers are doubles**: an `int` id above 2**53 changes value; `version: int` answers `0.0`.
- **No real types**: `Decimal`, `datetime`, enums and nested DTOs travel as JSON-ish values; a
  typed client (Go, Java, TypeScript) gets `map<string, Value>` and checks nothing at compile
  time; `buf breaking`, Envoy transcoding and Connect have nothing to read.
- **No streaming**: a large upload is one message under the 4 MiB limit; a Query with many items
  answers them all at once.
- **Services outside the framework** can call a `Struct` method only by building JSON by hand,
  and cannot generate a client that means anything.

`remote_execution` already carries `bytes`, `Decimal`, `datetime` natively over a gRPC stream —
but only between services that run the same Python code (§10). This PRD gives the **public**
gRPC door the same fidelity, readable by any language.

## Background

| Topic | What decides it |
|---|---|
| Building protobuf at runtime | `descriptor_pb2.FileDescriptorProto` + `DescriptorPool.Add` + `message_factory.GetMessageClass` (protobuf ≥ 4); `grpc.method_handlers_generic_handler` / a `GenericRpcHandler` looked up per call; `grpc_reflection` serving a given `pool` |
| Stable field numbers | proto3 encodes numbers, not names; protolock, protobuf-net, Buf's `reserved` guidance: a number is never reused, a deleted field is reserved by number and name |
| Breaking-change detection | `buf breaking` (WIRE_JSON), `buf lint` (AIP style); OpenAPI diff tools as the REST analogue |
| Types | Google well-known types (`Timestamp`, `Duration`, `Struct`, `Value`, wrappers), `google.type.Date`/`Money`/`Decimal`; proto3 `optional` for field presence |
| Streaming | gRPC unary / server / client / bidi streaming; flow control; deadlines across a stream; AIP-158 pagination vs streaming |
| Discovery | server reflection v1 / v1alpha (grpcurl, Postman, Evans); the analogue of `/openapi.json` |
| Edge | Envoy gRPC-JSON transcoding (`google.api.http`), Connect / gRPC-Web — all require typed messages |

### Runtime vs static — what each protocol does

| | REST (today) | gRPC `struct` (today) | gRPC `typed` (this PRD) |
|---|---|---|---|
| Contract built at runtime from the DTOs | OpenAPI document | descriptors for reflection, `Struct` methods | descriptors, **typed** message classes, handlers |
| Discovery at runtime | `GET /openapi.json` | reflection + `Describe` | reflection (now with the real types) + `Describe` |
| Static artifact for clients | `openapi.json` exported | `.proto` with `Struct` | `.proto` with typed messages **+ the lock** |
| What keeps it stable across versions | names in JSON | names in `Struct` | **committed field numbers** (§2) |
| Breaking-change check | OpenAPI diff | none | `lock --check` + `buf breaking` |

## The specification

**MUST**, **MUST NOT**, **SHOULD**, **MAY** as in RFC 2119.

### 0. What the framework builds at runtime, and what is static

At startup, from the buses and their declared bindings (`@grpc(...)`), the gateway **MUST** build
everything a server needs **in memory**:

1. one `FileDescriptorProto` per package (`{alias}.v{major}`), messages and enums derived from the
   DTOs (§3), numbered **only** from the lock (§2);
2. the message classes (`GetMessageClass` over its own `DescriptorPool`);
3. one handler per method — unary, server-, client- or bidi-streaming (§7) — that converts the
   message to the DTO, **calls the bus**, and converts the answer back;
4. server reflection (v1 + v1alpha) over that pool, and `Describe`.

A service **MUST NOT** need `protoc`, `grpcio-tools` or generated `*_pb2.py` files to serve the
typed contract.

What is **static** — produced by a command at development time and committed:

- **the lock** (§2): the only state the runtime cannot derive, because it is history;
- **the `.proto` export** (§6): rendered from the same descriptors, for clients that compile stubs.

The runtime **MUST** refuse to start (`ExposureRefused`) when a published field has no number in
the lock. It **MUST NOT** invent a number at runtime: a number decided at startup could differ
between two replicas or two deploys.

### 1. Contract modes

`GrpcGateway(..., contract="struct" | "typed")`, and per context with
`gateway.group(bus, contract="typed")`.

| Mode | Payload | `carries_bytes` | Streaming | For |
|---|---|---|---|---|
| `struct` (default, unchanged) | `google.protobuf.Struct` | `False` | no | grpcurl, Python ↔ Python, quick internal use |
| `typed` | one message per DTO | `True` | yes (§7) | typed clients in any language, interop, edge |

Both modes **MUST** share naming (PRD_15 §3.2), errors (`google.rpc.Status`), deadlines, auth via
metadata, idempotency, backpressure and health. Switching a context from `struct` to `typed`
changes the wire format: it is a new major of that package (`billing.v2`).

### 2. The lock — committed field numbers

A JSON file per context, next to the context's code, committed:
`domains/<ctx>/entrypoints/grpc.lock.json`.

```json
{
  "package": "billing.v1",
  "messages": {
    "billing.v1.StoreDocumentRequest": {
      "fields": {
        "customer_id": {"number": 1, "type": "int64"},
        "content": {"number": 2, "type": "bytes"},
        "tags": {"number": 7, "type": "repeated string"}
      },
      "reserved": [{"number": 8, "name": "note"}]
    }
  },
  "enums": {"billing.v1.Kind": {"INVOICE": 1, "RECEIPT": 2}}
}
```

Rules, enforced by `gateway.lock(path)` (writes) and `gateway.lock_check(path)` (CI, raises
nothing, answers the problems like `verify()`):

1. An existing field **keeps its number forever**, whatever its position in the class.
2. A new field takes `max(number, reserved) + 1`.
3. A removed field **moves to `reserved`** (number and name); neither is ever reused.
4. A renamed field is a removal plus an addition — unless declared with
   `Field(json_schema_extra={"grpc_renamed_from": "old"})`, which keeps the number.
5. A type change is refused unless wire-compatible (`int32` → `int64`, a message gaining fields);
   anything else needs a new field.
6. Enum values follow the same rules; `0` is always `{ENUM}_UNSPECIFIED`.
7. `lock_check` **MUST** fail on drift (a DTO field not in the lock, a lock field gone without
   being reserved). CI runs it, then `buf lint` and `buf breaking --against main` on the export.

### 3. Type mapping

| Python / Pydantic | Protobuf | Notes |
|---|---|---|
| `str` | `string` | |
| `int` | `int64` | out of range → `INVALID_ARGUMENT` on a request, `INTERNAL` (logged) on a response — a Python `int` has no bound, `int64` does |
| `float` | `double` | |
| `bool` | `bool` | |
| `bytes` | `bytes` | the reason `carries_bytes = True` |
| `Decimal` | `string` (exact) | `google.type.Decimal` MAY be chosen per gateway; never `double` |
| `datetime` | `google.protobuf.Timestamp` | naive datetimes refused at build |
| `timedelta` | `google.protobuf.Duration` | |
| `date` | `google.type.Date` | from `googleapis-common-protos`, already a dependency of `grpcio-status` |
| `UUID` | `string` | |
| `Enum` / `StrEnum` | `enum`, `0` = `_UNSPECIFIED` | numbered by the lock, not by member order |
| `Literal["a", "b"]` | `string` | validated by the DTO |
| nested DTO | `message` | same package, numbered by the lock |
| `list[X]` | `repeated X` | |
| `dict[str, X]` | `map<string, X>` | |
| `dict[str, Any]`, `Any` | `google.protobuf.Struct` / `Value` | for that field only |
| `X \| None` | `optional X` (proto3 presence) | |
| `A \| B` (two types) | refused at build | `oneof` is an open question |

**Presence.** proto3 scalars default to `0` / `""` / `false`, so a missing field and a zero are the
same on the wire. Every scalar field of a typed request **MUST** be declared `optional` so the
handler can tell "absent" from "zero"; an absent required DTO field then answers
`INVALID_ARGUMENT` with `BadRequest`, exactly as a missing JSON key does on REST.

### 4. Naming

Unchanged from PRD_15 §3.2: package `{alias}.v{major}`, service `{Alias}Service`, method = the DTO
name without `Command`/`Query`, messages `{Method}Request` / `{Method}Response`, fields verbatim
snake_case, nested DTOs by their class name. A collision fails the build.

### 5. The runtime build

#### 5.1 DTO ↔ message

The handler **MUST** convert field by field from the descriptor and the DTO's annotations — never
through JSON (`json_format` would turn `bytes` into base64 and `int64` into strings, the problem
`Struct` already has). The DTO is validated **once**, by Pydantic, after conversion; its errors
answer `INVALID_ARGUMENT` with `BadRequest.field_violations`.

#### 5.2 One path

Every typed handler **MUST** call the bus through `wire.through_the_bus` (PRD_15 §3.4) — auth from
metadata, deadline pre-check, idempotency, the Feature's span, `failure_kind` → `google.rpc.Status`.
A typed method and a `struct` method of the same use case behave identically except for the bytes
on the wire.

#### 5.3 Reflection and `Describe`

Reflection **MUST** serve the typed descriptors (grpcurl, Postman and Evans then show real types
without any `.proto`). `Describe` keeps the JSON Schema of each DTO and adds the lock version.

#### 5.4 Adding methods while running

A `GenericRpcHandler` looked up on every call lets a started server serve a method added later,
and `DescriptorPool.Add` makes reflection list it — proven (see Evidence). The gateway **SHOULD**
expose this for runtime use cases (PRD_06/07): when a new bus generation publishes a use case with
`@grpc()`, its file is added to the pool and its handler to the router, **only if** its fields
are in the lock (a stored use case carries its lock entries with its source). A removed use case
answers `UNIMPLEMENTED`; its numbers stay reserved. Following `registry.current` per request is
what PRD_07 calls an entrypoint that follows generations — not built today; this section depends
on it.

### 6. The static export

`gateway.write_proto_files(directory)` (exists for `struct`) **MUST** render the typed contract
from the same in-memory descriptors: one `{package path}/{alias}.proto` per package, the
well-known imports, `reserved` statements from the lock, streaming keywords. It **MUST** also write
a `buf.yaml` so a client repository can run `buf generate`, `buf lint` and `buf breaking`.

The export is to the typed contract what `openapi.json` is to REST: a file a client team pulls,
versions and compiles (Go, Java, TypeScript, Python stubs). It is never read by the server.
A project **SHOULD** publish it (a git repository, the Buf Schema Registry, a package) on each
release; the framework writes it, it does not publish it.

### 7. Streaming

A binding declares the shape: `@grpc(stream="server" | "client" | "bidi")`; absent means unary.

| Shape | The use case | Example |
|---|---|---|
| server | a `Query` whose `execute` answers an `Iterator[Response]` / `AsyncIterator[Response]` | a listing answered item by item, a long export |
| client | a Command whose one `bytes` field is assembled from the stream; the other fields come in the first message | uploading a PDF of any size in chunks |
| bidi | an ApplicationService that consumes an iterator and yields | phase 3, after server and client prove the model |

Rules:

1. **The bus runs a stream as one execution**: auth, the deadline pre-check and the interceptors
   run once at open; the Feature's span **MUST** stay open until the last item is sent or the
   stream fails — a span closed when `execute` returns a generator would measure nothing. This
   is a bus change, not only a wire change.
2. A client-streaming upload **MUST** have a size limit (`max_stream_bytes`, configurable) and
   answers `RESOURCE_EXHAUSTED` past it.
3. A failure mid-stream ends the stream with the `google.rpc.Status` of its `failure_kind`; items
   already sent stay sent.
4. `@idempotency.once` on a streaming method is refused at build (a stream has no single answer
   to replay).
5. The deadline covers the whole stream; cancellation by the client stops the iterator.
6. Streaming exists only in `typed` mode.

### 8. Bytes, by wire capability

`GrpcWire` in `typed` mode declares `carries_bytes = True` (the `Wire` port of PRD_14, built): a
Command with a `bytes` field is published as `bytes`, and `@grpc()` on it is no longer refused.
In `struct` mode it stays `False`. Nothing else in the gateway changes: the capability is the
wire's, the refusal the gateway's.

### 9. Services outside the framework

**Inbound — they call us.** A Go/Java/TypeScript/Python service compiles the exported `.proto`
(§6), or discovers the methods through reflection, and calls the typed methods like any gRPC
service. Auth travels in `authorization` metadata, idempotency in `idempotency-key`, tracing in
`traceparent` (PRD_15 §3.3).

**Outbound — we call them.** Calling an external gRPC service is a driven adapter in `adapters/`,
not an entrypoint. The framework **MAY** offer `GrpcService(target, proto_files | reflection=True)`:
a client that loads the remote contract (from their `.proto` or from their reflection, at runtime)
and calls a method with a DTO, converting with §3 in the other direction — so an adapter needs no
generated stubs either. A project **MAY** always use generated stubs instead.

**Importing their types.** A framework DTO **MAY** reference an external message only through its
own DTO (copied fields); importing a foreign `.proto` into the exported contract is out of scope.

### 10. Remote execution stays its own door

| | `remote_execution` | `GrpcGateway(contract="typed")` |
|---|---|---|
| Who | Sincpro Python services running the same code | anyone, any language |
| Contract | none to publish: the DTO class itself | the `.proto` + lock, versioned |
| Payload | pickled values, 1 MiB chunks, one `stream_stream` | typed protobuf messages |
| Scope | a whole bounded context, location-transparent | the declared use cases only |

Rule 1 of PRD_15 §0 holds: the typed gateway **MUST NOT** host remote execution, and remote
execution **MUST NOT** depend on the lock. A service may run both, on one port on purpose
(`open_host([...]).mount(server)`).

### 11. Conformance — what the release MUST prove

- Round trip through a **protoc-compiled client** against the runtime server: `bytes` (with `\x00`
  and `\xff`), `int64` above 2**53, `Decimal` exactly, `Timestamp`, enum, nested, repeated, map,
  optional absent vs zero.
- Lock: a field inserted mid-class keeps every other number; a removed field is reserved and its
  number never reused; a type change refused; a field not in the lock refuses the build.
- The exported `.proto` passes `buf lint` and `buf breaking` against the previous export.
- Reflection lists typed methods; grpcurl calls one with no `.proto`.
- Parity with `struct`: same status, details and trailers per failure kind; the Feature's span.
- Server streaming: span open for the whole stream; a mid-stream failure ends with its status;
  client cancellation stops the iterator. Client streaming: the size limit; the assembled Command
  validated once.
- A method added to a running server is served and reflected (§5.4).

### 12. Release phases

| Phase | Contents |
|---|---|
| **1** | §1 `typed` mode, §2 lock with `lock` / `lock_check`, §3 mapping, §5.1–5.3, §6 export + `buf.yaml`, §8 `carries_bytes`, §11 for these |
| **2** | §7 server and client streaming, including the bus change for a stream's execution |
| **3** | §5.4 hot add (after PRD_07's generation-following entrypoints), §9 `GrpcService` outbound client, bidi streaming, `google.api.http` for Envoy transcoding, Connect |

## Evidence

A proof of concept (outside the repo, against protobuf 7.36 and grpcio 1.83) showed:

- A Pydantic DTO + a lock → `FileDescriptorProto` → `GetMessageClass` → a server with unary,
  server-streaming and client-streaming methods, **with no `protoc`**.
- The same descriptor rendered as `.proto` text, compiled by `grpc_tools.protoc` into stubs, used
  by an "external" client: `bytes` with `\x00\xff`, `int64` = 2**60 + 7, `Decimal` as an exact
  string, `Timestamp`, enum, nested and repeated messages, a `reserved` field — all round-tripped.
- A field declared later in the class (`tags`) kept the number the lock gave it (7), not its
  position.
- An `int` result above `int64` was refused by protobuf (`Value out of range`) — the reason §3
  maps it to a failure kind instead of letting it crash.
- A started server gained a method and its descriptor (`GenericRpcHandler` + `DescriptorPool.Add`)
  and server reflection served it, with no restart (§5.4).
- protobuf 7 removed `FieldDescriptor.label` (use `is_repeated`): the converter must target the
  current descriptor API.

## Decisions

- **Runtime build, static export, one derivation.** The server never reads a `.proto`; clients
  never need the server's code. Both come from the DTO plus the lock, so they cannot disagree.
- **The lock is the only committed state.** Field numbers are history, and history cannot be
  derived from a class; everything else is.
- **No numbers invented at runtime.** Determinism across replicas and deploys beats convenience;
  `lock()` is a development command, `lock_check` a CI gate.
- **`struct` stays the default.** Typed is opt-in per context and a new package major, so no
  deployed client breaks.
- **Streaming changes the bus, not only the wire.** A stream is one execution with one span; doing
  it only in the wire would observe nothing.
- **Outbound calls are adapters.** Calling another company's gRPC service is a driven port; the
  gateway publishes, it never calls out.

## Open questions

1. **Lock location.** One `grpc.lock.json` per context (proposed), or one per service next to the
   gateway?
2. **`oneof`.** Map a discriminated Pydantic union (`Field(discriminator=...)`) to `oneof`, or keep
   unions refused?
3. **`Decimal` default.** Exact `string` (proposed, no dependency) or `google.type.Decimal`?
4. **Hot add (§5.4).** Worth building before PRD_07's generation-following entrypoints exist, or
   only after?
5. **Outbound `GrpcService`.** Part of the framework, or a recipe in the docs using generated stubs?
6. **Response presence.** Mark response scalars `optional` too (clients see "unset" vs `0`), or
   only requests?
