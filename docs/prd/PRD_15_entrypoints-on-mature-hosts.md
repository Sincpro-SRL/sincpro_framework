# PRD_15: Entrypoints on mature hosts — REST on FastAPI, gRPC on grpcio, JSON-RPC, queues on FastStream

- **Status**: phase 1 built — the two doors (§0), the transport core (§0.1), REST on FastAPI (§2),
  gRPC with AIP names (§3), JSON-RPC without the layer (§4) and queues on FastStream (§5), each
  on PRD_14's declared exposure. Every section's status paragraph says what is left for phase 2.
  Builds on PRD_12 (gateways, catalog), PRD_14 (declared exposure) and PRD_13 (idempotency).
- **Depends on**: `Gateway`/`Catalog`, `failure_kind`, `AccessControl` and its transports, the
  observability door (`process`), `@idempotency.once`, `IdempotencyRecords`, `events.faststream`.
- **Philosophy**: the application layer is the product — use cases and their non-functional
  components (access, idempotency, caching, observability). An entrypoint is an optional adapter
  that **marries the mature host of its protocol** instead of re-implementing it: FastAPI for REST,
  Google's grpcio for gRPC, the JSON-RPC 2.0 + OpenRPC specifications, FastStream for queues. Each
  entrypoint ships an automatic mode that needs no code, and hands full control to a project that
  wants its own routes, servicers or subscribers — without losing any component, because every path
  calls the bus.

## Problem

PRD_12 made every wire publish the catalog, and PRD_14 lets a use case declare its exposure. What
is still missing is that each wire speaks **its protocol the way its ecosystem expects**:

- REST is RPC-over-HTTP (`POST /billing/command-issue-invoice`) on a hand-built Starlette app; a
  project that writes one FastAPI route of its own gets a second OpenAPI document, and no reusable
  auth or error handling.
- gRPC answers `Struct` only: money and `int64` ids travel as doubles, typed clients, transcoding
  and breaking-change detection get nothing; errors carry text instead of `google.rpc.Status`;
  deadlines are ignored; no `NOT_FOUND`; reflection is v1alpha only; no backpressure.
- JSON-RPC names carry the layer (`billing.features.CommandIssueInvoice`), so promoting a Feature
  to an ApplicationService renames a public method; batches have no limit.
- **Queues lose messages today**: FastStream's default ack policy for Kafka is `ACK_FIRST`
  (at-most-once) and Rabbit/Redis `REJECT_ON_ERROR` discard a failed message — while
  `docs/events/brokers.md` promises at-least-once. Commands cannot be consumed at all.

## Background — the research behind each wire

| Wire | Sources that decide it |
|---|---|
| REST | Google AIP-121/133/134/136/154/158, Zalando and Microsoft REST guidelines, Stripe; RFC 9110 (status, `Retry-After`, `If-Match`), RFC 9457 (problem details), RFC 7396 (merge patch), RFC 9745/8594 (deprecation); the IETF `Idempotency-Key` draft; FastAPI's `APIRouter`, `add_api_route`, query-param models, exception handlers; FastAPI-Users (generated routers + exported dependencies); Litestar and ASP.NET groups |
| gRPC | AIP-121/136/185/190/191/193/154/155/158; Buf style guide, `buf lint`, `buf breaking` (WIRE_JSON); grpc.io status codes, deadlines, retry service config, metadata, health, reflection; `grpcio-status`; protolock and protobuf-net for committed field numbers; Spring gRPC for full control |
| JSON-RPC | the JSON-RPC 2.0 specification; OpenRPC 1.4; MCP's Streamable HTTP (202 for notifications, `_meta`); Ethereum EIP-1474 and LSP for namespacing and error ranges; fastapi-jsonrpc's typed error catalogue |
| Queues | FastStream `AckPolicy`, middlewares, AsyncAPI 3.0, test brokers; CloudEvents 1.0 (+ distributed tracing, authcontext); Dapr's SUCCESS/RETRY/DROP verdict; Wolverine and MassTransit (retry, error queue, inbox, outbox); microservices.io idempotent consumer and transactional outbox; RabbitMQ validated `user-id` and quorum `delivery-limit`; OTel messaging semantic conventions |

## The specification

**MUST**, **MUST NOT**, **SHOULD**, **MAY** as in RFC 2119.

### 0. Two ways a bus is reached — and they are not the same thing

| | **Remote execution** (`remote_execution`) | **Entrypoints** (PRD_14 + this PRD) |
|---|---|---|
| What for | the project's own services reaching each other: `billing(dto)` runs on another machine and the caller's code does not change | publishing use cases to **others** — clients, third parties, frontends, agents |
| What travels | a whole bounded context, automatically — location transparency, the context map decides | only what is declared, use case by use case |
| The contract | internal: both ends run the same code; a typed binary payload, errors raised as their own class | public: documented (OpenAPI, OpenRPC, reflection, AsyncAPI), versioned, deprecated with notice |
| Transports | `http://` — the standard library, no extra, available everywhere; **`grpc://` recommended** (streaming chunks, one connection, deadlines) | REST on FastAPI, gRPC on grpcio, JSON-RPC, MCP, queues |
| Who may call | the project's services: a service identity (`ServiceTokenProvider`), mTLS or the network | anyone the declared access allows (`@auth.requires` / `@auth.public`) |
| Where it is specified | `docs/entrypoints/bounded-contexts-across-services.md` | PRD_14 (what is exposed) and this PRD (how each protocol carries it) |

Rules that keep them apart:

1. An entrypoint gateway **MUST NOT** host remote execution implicitly. Today `GrpcGateway` also
   answers `/sincpro.Contexts/Execute` — the door that runs a whole context — on the same server as
   the public surface; it moves out: remote execution is served by `bus.serve(...)`, or mounted on a
   server **explicitly** (`gateway.host_contexts([...])`), never as a side effect.
2. Remote execution **MUST NOT** be used to publish to a third party: it has no per-use-case
   exposure, no public document and no versioning — it is the same codebase talking to itself.
3. The two share the application layer and a **transport core**, nothing else: the same bus, the
   same components (access, idempotency, caching, observability), the same mechanics of each
   technology — different doors.

#### 0.1 Layers and packages

```text
  exposing (public)                                   reaching buses (internal)
  entrypoints/<technology>                             remote_execution
  fastapi · grpc · jsonrpc · mcp · faststream          context map · open host · typed payload · bus.serve
                 │          neither imports the other          │
                 └────────────────────┬────────────────────────┘
  transport/   the mechanics of each technology, exposing nothing
               grpc: the server (pool, backpressure, keepalive, sizes), health,
                     metadata ↔ request context, trace headers, credentials, graceful stop
               http: ASGI credentials, context and trace headers, status codes
               failures: the one classification of failures (`failure_kind`)
                                      │
  application  the bus · use cases · access · idempotency · caching · observability
```

| Package | Is | Owns | May import |
|---|---|---|---|
| `entrypoints/<technology>` | one concrete technology married: `fastapi`, `grpc` (grpcio), `jsonrpc`, `mcp` (FastMCP), `faststream` | the exposed surface: routes, services, methods, tools, consumers; the public document; the full-control helpers | `entrypoints/exposure`, `entrypoints/catalog`, `transport`, the application layer |
| `entrypoints/exposure` | protocol vocabulary, technology-free | the registry and the bindings — `rest`, `grpc`, `rpc`, `mcp`, `queue` — PRD_14 | the application layer only |
| `entrypoints/catalog` | what every technology reads | `Catalog`, `Gateway`, precedence and build validation (PRD_14 §7) | `exposure`, the application layer |
| `remote_execution` | the project talking to itself | context map, open host, the chunked typed payload, `bus.serve`, the callers' adapters | `transport`, the application layer |
| `transport` | the mechanics of each technology | server factories, health, codecs of metadata/headers, credentials, deadlines, graceful stop, `failure_kind` — reflection, introspection and documents are **exposure** and stay in the entrypoint | the application layer only |

- A binding is **per protocol** (`@rest.post(...)`), a host is **per technology** (FastAPI): changing
  the REST host changes no use case.
- `bus.serve(...)` builds its server from `transport.grpc` and mounts **only** the open host and
  health — never the public catalog, which today it publishes as a side effect.
- One port for both, when a deployment wants it, is explicit:

  ```python
  server = GrpcGateway([billing]).server()    # the public API: its declared surface
  open_host([billing]).mount(server)          # the internal door, on purpose
  ```

- The dependency rules **MUST** be enforced by an architecture test (`testing.layer_violations`):
  `entrypoints` and `remote_execution` never import each other; `transport` imports neither; no
  technology's library is imported outside its own package and `transport`.

### 1. What every entrypoint shares

#### 1.1 Three ways to use a wire

| Mode | The project writes | The framework gives |
|---|---|---|
| **auto** | nothing: `Gateway([billing, sales])` | every route, method, tool or subscriber, its document, its errors — PRD_12's catalog, or PRD_14's declared surface |
| **mix** | narrows, groups, overrides, or takes the generated router / service per context and composes it | the same surface, shaped by the composition (PRD_14 §7 precedence) |
| **full control** | its own routes, servicer, method or subscriber | one **call helper** per wire that runs the exact path the generated code runs |

#### 1.2 One call path

Every mode of every wire **MUST** run the same path, so a hand-written entrypoint can neither
skip nor re-implement a component:

```text
decode the request → read credentials → [inbox, on queues] → bus(dto) → classify the outcome → encode
                                                              └ access guard → idempotency → caching →
                                                                interceptors → execute   (PRD_14 §10)
```

| Wire | The call helper a full-control entrypoint uses |
|---|---|
| REST | `bus_call(bus)` — a FastAPI dependency returning `await call(dto)` |
| gRPC | `bus_call(context, bus, dto, to_response=...)` |
| JSON-RPC | `dispatch(surface, payload, credentials=, headers=)` |
| Queue | `gateway.consume(message, as_=Command, decode=...)` |
| MCP | `McpGateway(...).tools()` — each `McpTool` with its operation — and `tool_function(dto, name, description, run, bus)`, the function `server()` registers (**built**; parity tested: a tool call answers what the bus answers, and a `destructive=False` tool still needs the permission) |

- A helper **MUST** call the bus, never `execute`: the access guard, idempotency, caching, the
  Feature's span and Sentry apply by construction.
- Authentication and the bus call **MUST** run in the same thread: identity is a `ContextVar`, and
  a value set in one worker thread is invisible to the next (FastAPI runs each sync dependency in a
  copied context).
- A test **MUST** prove parity: the same input through the generated path and through the helper
  answers the same status, body and span.

#### 1.3 One classification of failures, each wire's own encoding

`failure_kind` (PRD_12) stays the single classification, and gains `NOT_FOUND`, `IN_PROGRESS`,
`KEY_REUSED`, `UNAVAILABLE` and `EXHAUSTED`. Every wire carries the kind and a stable `reason`
(UPPER_SNAKE, the error's class) so a client switches on meaning, not on a number:

| Kind | REST | gRPC | JSON-RPC | Queue verdict |
|---|---|---|---|---|
| invalid | 422 (400 unreadable) | `INVALID_ARGUMENT` + `BadRequest` | -32602, `data` = errors | dead-letter |
| unauthenticated | 401 + `WWW-Authenticate` | `UNAUTHENTICATED` | -32001 | dead-letter + security log |
| permission denied | 403 | `PERMISSION_DENIED` | -32003 | dead-letter + security log |
| not found | 404 | `NOT_FOUND` | -32004 | dead-letter |
| conflict (stale, duplicate) | 409 (412 with `If-Match`) | `ABORTED` / `ALREADY_EXISTS` | -32009 | retry with delay |
| in progress (idempotency) | 409 + `Retry-After` | `ABORTED` + `RetryInfo` | -32029 | retry with delay |
| key reused | 422 | `FAILED_PRECONDITION` | -32022 | dead-letter |
| domain | 422 | `FAILED_PRECONDITION` + `PreconditionFailure` | -32010 | dead-letter + `Fault` event |
| exhausted | 429 + `Retry-After` | `RESOURCE_EXHAUSTED` | -32029 | retry with delay |
| unavailable | 503 + `Retry-After` | `UNAVAILABLE` | -32000 | retry |
| internal | 500, nothing of the inside | `INTERNAL`, nothing of the inside | -32603 | retry, then dead-letter |

#### 1.4 Tracing — one root, the host's instrumentation

The framework's traces are OpenTelemetry already. An entrypoint **MUST NOT** open a second root:

- When the host's official OpenTelemetry instrumentation is installed
  (`opentelemetry-instrumentation-fastapi`/`-asgi`, `-grpc`), it opens the transport span and the
  bus span is its child.
- Without it, the entrypoint opens the transport span itself from the incoming context
  (`traceparent`), as it does today.
- A queue consumer opens an OTel messaging `process` span **linked** to the producer's context (a
  link by default, a parent for single messages), with `messaging.system`,
  `messaging.destination.name`, `messaging.message.id`.

#### 1.5 Composition of bounded contexts

Each bus is a bounded context, and each wire has its own unit of composition — the PRD_14 group:

| Wire | Unit per context | Composing N contexts |
|---|---|---|
| REST | one `APIRouter`, prefix + tag | one FastAPI app, `include_router` each, one OpenAPI document; `mount` only for a separate audience (its own document) |
| gRPC | one package `alias.v1`, one `AliasService` | one server hosting every package; reflection and health list all |
| JSON-RPC | one namespace `alias` | one endpoint, one OpenRPC document tagged per context |
| Queue | a channel prefix `alias.`, a consumer group | one broker; each context its own group for events |

### 2. REST on FastAPI

FastAPI becomes the REST host (`[fastapi]` extra): it validates each body once, is the one OpenAPI
generator for generated and hand-written routes, and gives full-control routes `Depends`. The route
table (`RestRoute`, `build_routes`) stays wire-neutral and core-free; the Starlette emitter is
frozen as the minimal host with no new features.

#### 2.1 Two profiles

| Profile | Paths | For |
|---|---|---|
| `resource` — **the default** | only what is declared (PRD_14 DECLARED), shaped as resources (§2.2) | a public API: a third party, a frontend, an HTTP cache or gateway |
| `rpc` — on request, with CATALOG mode | `POST /{group}/{kebab(Dto)}`, a `Query` on `GET` (+ `POST` for a long criteria) — today's paths | a quick internal surface or an admin tool over HTTP; the Twirp/Connect model, documented as RPC over HTTP, never as REST. Service-to-service calls are remote execution (§0), not this |

#### 2.2 Resources — what is declared, what is derived

Most DDD Commands are **custom methods**, not CRUD: they carry an intent the resource shape must
keep (AIP-136, Stripe). The profile uses `/{id}/verb` — `:` in a path breaks some gateways and
generators.

| Use case | Binding | Answer |
|---|---|---|
| creates | `@rest.post("/invoices", status=201, location="/invoices/{number}")` | 201 + `Location` |
| reads one | `@rest.get("/invoices/{invoice_id}")` on a `Query` | 200 |
| lists | `@rest.get("/invoices")` on a `Query` carrying `Criteria` | 200 with what the use case answers — its repository's page (`items`, `cursor`, `count`) |
| changes part | `@rest.patch("/invoices/{invoice_id}", body="merge-patch")` | 200 with the resource |
| removes | `@rest.delete("/invoices/{invoice_id}")` with a `None` response | 204 |
| a domain verb | `@rest.post("/invoices/{invoice_id}/cancel")` | 200 (202 when asynchronous) |
| a singleton | `@rest.get("/billing-settings")` | 200 |

`RestBinding` gains `location`, `body` (`"json"` | `"merge-patch"`) and `concurrency` (`"if-match"`).
Guessing a standard method from a name (`CommandCreateX`) is refused: a rename would change the
public contract.

**Build rules** (added to PRD_14 §7): `{field}` in `path` is a Command field and in `location` a
response field; `GET` only on a `Query`; `DELETE`/204 only with a `None` response; `PATCH` only with
merge-patch or optional fields. **The method is the class, never the name**: every DTO that inherits
`Query` — directly or through a project's own base — is derived `GET` (the rpc profile also
answers `POST` on it, for a criteria too long for a URL); anything else is derived `POST`.

#### 2.3 HTTP semantics

| Concern | Rule |
|---|---|
| Errors | `application/problem+json` (RFC 9457): `type`, `title`, `status`, `detail`, `instance`, plus the extensions `kind`, `reason`, `trace_id`, `errors[{pointer, detail}]`; a 500 carries no `detail`. FastAPI's own 422 schema in the document is replaced by `Problem` |
| Idempotency | the `Idempotency-Key` header reaches the bus context; the Command's `idempotency_key()` stays authoritative and the header is the key only when the Command has none; missing when required → 400, `KeyReused` → 422, `AlreadyInProgress` → 409 + `Retry-After` |
| Pagination | the domain's, not a second one on the wire: a listing `Query` carries `Criteria` (`pagination=Pagination(limit=, strategy=Cursor(token=) or Offset(rows=))`, read from the query string), and answers its repository's page — `items`, `cursor` (`None` at the end), `count`. A REST envelope with other names (`page_size`/`page_token`, AIP-158) would page twice; not built, by decision |
| Versioning | the major version in the group's prefix (`/v1`) |
| Concurrency | `ETag` on a resource, `If-Match` on PATCH/DELETE → 412 on mismatch (phase 2) |
| Deprecation | `Deprecation` + `Sunset` + `Link rel="deprecation"`; `deprecated: true` in the document (phase 2) |
| Caching | `Cache-Control`, `ETag`/`If-None-Match` → 304 on a `@caching.keeps` Query, `Vary: Authorization` (phase 3) |
| Long-running | 202 + an operation resource to poll (phase 3) |

#### 2.4 FastAPI, done right

- A Command's body is annotated with its DTO — FastAPI validates once and hands the instance, and
  the endpoint calls the bus directly, never the executor that validates again. Path fields merge
  into the DTO before its one validation.
- A `Query` on `GET` reads through a dependency that decodes the JSON `criteria` and validates once;
  its parameters are documented through `openapi_extra`.
- `response_model` is the declared response; `operation_id` is the DTO's name (qualified by the
  group on a clash); `separate_input_output_schemas=False`.
- `install_problem_handlers(app)` registers the problem handlers for every failure kind,
  `RequestValidationError` and Starlette's `HTTPException` (404/405 too).
- The bus is synchronous: authentication and the bus call run in one worker thread
  (`anyio.to_thread`), whose pool size is a setting (40 by default).
- `BackgroundTasks` **MUST NOT** carry domain work: it runs after the answer, outside idempotency.

#### 2.5 Full control

```python
app = FastAPI(separate_input_output_schemas=False)
install_problem_handlers(app)
app.include_router(api.router(billing, exclude=[CommandIssueInvoice]), prefix="/v1/billing")

@router.post("/invoices", status_code=201, operation_id="CommandIssueInvoice",
             responses=problem_responses(billing, CommandIssueInvoice),
             openapi_extra=api.operation_extra(billing, CommandIssueInvoice))
async def issue(cmd: CommandIssueInvoice, response: Response,
                call: BusCall = Depends(bus_call(billing))) -> Issued:
    issued = await call(cmd)
    response.headers["Location"] = f"/invoices/{issued.number}"
    return issued
```

Exported: `bus_call`, `acting_credentials` (an async dependency, no I/O), `request_context`
(trace and `Idempotency-Key` into the context), `install_problem_handlers`, `problem_responses`,
`operation_extra`. `api.verify(app)` walks the app's routes and runs PRD_14's clash checks over the
generated and the hand-written ones together. A FastAPI dependency is never authorization:
`AccessControl` inside the bus stays the only guard.

**Status — REST on FastAPI, phase 1: built** (`sincpro_framework.entrypoints.fastapi`,
`[fastapi]` extra; `docs/entrypoints/fastapi.md`). `FastApiGateway` (`wire = "rest"`, its port
`FastApiWire`): profiles `resource` (DECLARED, default) and `rpc` (CATALOG, today's paths, a
`Query` also on `POST` `_by_body`); derivation (Query→GET, else POST; 204 for a `None` response;
operationId the DTO, `{alias}_{Dto}` on a clash); refusals (method + path clash with `{x}`
normalised, repeated operationId, `wrap`); one `APIRouter` per context (`/{version}{prefix}`),
`router(bus, exclude=)`, `app()`. Validate once (body = DTO, path fields merged before its one
validation; `GET`/`DELETE` via a dependency, criteria as JSON) — spy-tested. `bus_call` with
authentication and `bus(dto)` in one worker thread — contextvar regression and parity tested.
`install_problem_handlers` (RFC 9457 for every kind, `RequestValidationError`, 404/405;
`Retry-After`, `WWW-Authenticate`; `Problem` replaces `HTTPValidationError` in the document);
201 + `Location`, 204 empty; `Idempotency-Key` into the bus context, 400 when a `once` use case's
Command has no `idempotency_key()` and the header is missing, concurrent retries run once;
`traceparent` passed through; exports `bus_call`, `acting_credentials`, `request_context`,
`problem_responses`, `operation_extra`, `api.verify(app)`; security schemes and
`x-sincpro-requires` from the shared `rest.openapi` fragments. Listings are a `Query` with `Criteria`
(no REST envelope, by decision). **Not built**:
`ETag`/`If-Match` and merge patch (a binding declaring `concurrency="if-match"` or
`body="merge-patch"` is refused), deprecation headers, Schemathesis and `openapi-spec-validator`
(not installed — structural checks and `$ref` resolution instead). The header is the run's key
when the Command has no `idempotency_key()` (`caching.IDEMPOTENCY_KEY` in the bus context).

### 3. gRPC on grpcio

#### 3.1 Contracts

| Contract | What travels | For | Phase |
|---|---|---|---|
| `struct` — default | `google.protobuf.Struct`, documented as "JSON over gRPC" | Python to Python, grpcurl, internal | built; its fixes phase 1 |
| `typed` — opt-in per context | real messages generated from the DTOs, numbered by a **committed lock file** | typed clients, grpc-web/Connect, Envoy transcoding, `buf breaking` | phase 2 |

The typed contract keeps the DTO as the source of truth and turns silent renumbering into a
reviewed diff: an existing name keeps its number forever, a new field takes `max + 1`, a deleted
field moves to `reserved` (number and name), a type change is refused unless wire-compatible.
`lock --check` fails on drift; CI runs `buf lint` and `buf breaking --against main` at WIRE_JSON.
Types: `int` → int64, `Decimal` → string or `google.type.Money`, `datetime` → Timestamp, `Enum` →
enum with `_UNSPECIFIED = 0`, `bytes` → bytes, `Any`/`dict` → Struct for that field only.

#### 3.2 Naming — AIP by default

| Element | Rule |
|---|---|
| package | `{alias}.v{major}` |
| service | `{Alias}Service` — one per context; `@grpc(service=...)` splits |
| method | the DTO's name without `Command`/`Query`, PascalCase (`CommandIssueInvoice` → `IssueInvoice`) |
| messages | `{Method}Request`, `{Method}Response` |
| fields | verbatim snake_case |

The `Features`/`AppServices` services are a layering detail leaking into a public contract and are
**removed** — no legacy naming is kept. A collision after stripping fails the build.

**Built** (`entrypoints/grpc/naming.py`, `GrpcWire`): `GrpcGateway` is a PRD_14 gateway (`wire =
"grpc"`, `exposure=` DECLARED by default, `unguarded=`, `port=` a `GrpcWire`); package, service
(`@grpc(service=...)` splits) and method as above, group `package`/`version` honoured; a collision
after stripping, a non-identifier name or a framework-reserved service fails the build.
Reflection, `Describe`, the `.proto` export (`billing/v1/billing.proto`), health and the client use
these names; the Struct contract is unchanged. Messages `{Method}Request`/`Response` belong to the
typed contract (phase 2) and are not built.

#### 3.3 Server behaviour

| Concern | Rule | Phase |
|---|---|---|
| Errors | `abort_with_status(google.rpc.Status)` with `ErrorInfo{reason, domain=package, metadata}` always, `BadRequest` for validation, `PreconditionFailure` for rules, `RetryInfo` when retryable (AIP-193; `grpcio-status` joins `[grpc]`) | 1 |
| Deadlines | the deadline (`time_remaining()`) enters the context; a call past it answers `DEADLINE_EXCEEDED` without running; adapters read it for their own timeouts | 1 |
| Backpressure | `maximum_concurrent_rpcs` set (2 × workers by default) → `RESOURCE_EXHAUSTED` past it | 1 |
| Reflection | v1 and v1alpha both | 1 |
| Health | every service name and `""` answer; `NOT_SERVING` on SIGTERM before `stop(grace)` — **built** (`GrpcGateway.drain()`, flipped by `run()`'s signal hook). One readiness for the whole process: a service `NOT_SERVING` on its own (its bus failing while the others serve) is phase 2 — probes and balancers read `""` | 1 / 2 |
| Keepalive, message size | sane server defaults behind a load balancer; `max_connection_age` so rolling deploys rebalance; the 4 MiB limit documented and configurable — **built** (`max_message_bytes=`, `max_connection_age=` 300 s, keepalive options, `wire.server_options`) | 1 |
| Metadata | `authorization`, `idempotency-key` (a `request_id` field also accepted, AIP-155), `traceparent`/`tracestate`, `x-request-id`, `sp-ctx-<key>` | 1 |
| Retries | `recommended_service_config()`: retry `UNAVAILABLE` always; `ABORTED`/`RESOURCE_EXHAUSTED` only on idempotent methods (`@idempotency.once`, a `Query`) — the framework knows which | 2 |
| `grpc.aio` | an option, the bus called through `asyncio.to_thread` (which copies context) | 2 |
| Streaming, transcoding, LRO | server-streaming for iterable Query results (typed only), `google.api.http` for Envoy, `google.longrunning` | 3 |

#### 3.4 Full control

```python
class BillingServicer(billing_pb2_grpc.BillingServiceServicer):
    def IssueInvoice(self, request, context):
        return bus_call(context, billing, CommandIssueInvoice(customer_id=request.customer_id),
                        to_response=lambda r: billing_pb2.IssueInvoiceResponse(number=r.number))

server = grpc.server(pool, interceptors=framework_interceptors())
billing_pb2_grpc.add_BillingServiceServicer_to_server(BillingServicer(), server)
GrpcGateway([billing]).mount(server, reflection_extra=[billing_pb2.DESCRIPTOR])
```

A hand-written servicer only adds a servicer: it never re-implements auth or errors, and a method
path the generated gateway also serves fails the build.

**Built**: `bus_call(context, bus, dto, to_response=None, *, domain=None)` runs the generated
methods' own path (`wire.through_the_bus`) — parity test on status, details, trailers and the
Feature's span; `dto` may be a factory so its validation error is `INVALID_ARGUMENT`.
`framework_interceptors()` = deadline pre-check + call log. `mount(server, reflection_extra=[...])`
merges the hand-written files into one reflection and refuses (`ExposureRefused`) a hand-written
path the gateway serves. The clash is detected from `reflection_extra` — a servicer mounted without
its descriptor is not seen (grpcio exposes no handler list).

### 4. JSON-RPC

| Concern | Rule | Phase |
|---|---|---|
| Names | `{namespace}.{operation}` — the group (the alias by default) and the DTO's name without `Command`/`Query`, snake_case: `billing.issue_invoice`. No layer, in every mode — `{alias}.{layer}.{Dto}` is removed; a breaking version in the namespace (`billing.v2.issue_invoice`) | 1 |
| Reserved | only `rpc.discover` uses `rpc.`; a binding under it is refused | 1 |
| Batches | a maximum size (50 by default) and body size (1 MB); calls may run in any order; an all-notification batch answers nothing | 1 |
| HTTP | 200 for any JSON-RPC answer, errors included; 204 when nothing is answered; 401 + `WWW-Authenticate` for an unauthenticated single request; 413 over a limit; 415 for a wrong content type | 1 |
| Errors | the catalogue of §1.3 published once in OpenRPC `components.errors` and referenced from each method; every `data` carries `kind`, `reason`, `retryable` — the numbers above -32000 mean other things in other APIs | 1 |
| Document | OpenRPC 1.4, `paramStructure: "by-name"`, a tag per context, validated against the meta-schema | 1 |
| Metadata | `traceparent` and the correlation id as HTTP headers; `params._meta` (MCP's convention) as an alternative to the body's `context` sibling, which is kept as a documented `x-` extension | 2 |
| Idempotency | `Idempotency-Key` header or `params._meta.idempotency_key` | 2 |
| Examples, deprecation, access | `examples`, `deprecated` + `x-sunset`, `x-sincpro-requires`, `x-idempotent` | 2 |
| WebSocket and server push | subscriptions to `DomainEvent`s as notifications | 3 |

Full control: `dispatch(gateway.surface(), payload, credentials=..., headers=...)` answers the reply
(or `None` for nothing), under any framework the project chooses.

**Status — JSON-RPC, phase 1: built.** `RpcGateway` sets `wire = "rpc"` and resolves the PRD_14
surface through its `JsonRpcWire` port (DECLARED by default, CATALOG on request, `unguarded=`,
`port=` a `JsonRpcWire`): names `{namespace}.{operation}` derived from the group (alias, snake_case;
`namespace=`/`version=`) and the DTO, declared/override names validated (dotted snake_case, not
`rpc.`), clashes after derivation refused at build; the old `{alias}.{layer}.{Dto}` names are gone.
The OpenRPC document is tagged per context and carries `x-sincpro-requires`, `x-idempotent`,
`deprecated`/`x-sunset`. `dispatch(surface, payload, credentials=, headers=)` and
`http_status(surface, reply)` are the path the gateway's own route runs, proven by a parity test
(`tests/entrypoint/test_jsonrpc_naming.py`), including "a name is unchanged when a Feature
becomes an ApplicationService". Not built: `RpcBinding.notification` is recorded, not served
(PRD_14 phase 3); `_meta`, `Idempotency-Key`, examples (phase 2).

### 5. Queues — a wire of its own, on FastStream

Queues are an entrypoint (`entrypoints/queue`, a PRD_14 `Wire`), built on `events/faststream`.
The events module keeps how a fact leaves the process — `Publisher`, codec, trace carrier, outbox;
the entrypoint owns what outsiders may make this process do — exposure, the producer's identity,
the inbox, settling, dead letters, AsyncAPI and the manifest.

#### 5.0 What exists, what stays, what is new

| Piece | Today | In this release |
|---|---|---|
| `Publisher` / `AsyncPublisher`, `FastStreamQueue` (`put`, `aput`), `keyed_by_entity` | built — how an event leaves the process | **unchanged** |
| `Subscriber(*buses)` — every bus that registered an event runs it | built | **unchanged** |
| `subscribe(broker, Subscriber(...), channel_of_name=)` — one subscription per event name, routed by the event header | built | **unchanged signature**, now the shortcut over `QueueGateway` for events (§5.1): one settlement, inbox and dead letter for both — **fixed** (§7, issue 1) |
| `BackgroundQueue`, `SyncQueue` — the in-process queues | built, at most once by nature | **unchanged** |
| consuming a **Command** from a queue | does not exist | **new**: `@queue.consumes(...)`, declared only |
| a gateway over the broker (`QueueGateway`), the inbox, dead letters, CloudEvents, AsyncAPI, `consume()` | does not exist | **new** |

`subscribe()` stays the one-line way to hear events and is now `QueueGateway` over the
subscriber's buses — their events only, in one consumer group — so there is one implementation of
settling, the inbox and dead letters. The new pieces add Commands and the reliability a public
consumer needs, without changing what a project already wrote.

#### 5.1 What is consumed

| Kind | Declared | Semantics |
|---|---|---|
| a DomainEvent | **nothing** — registering the Feature for it (`@bus.feature(InvoiceIssued)`) is the declaration; `@queue.hears(group=, channel=)` only moves it | publish/subscribe; each context in its own consumer group (the group's `namespace`, else the alias) |
| a Command | `@queue.consumes("billing.invoices.issue", producers=("svc:sales",), max_attempts=5, concurrency=4)` — or `bind` | point to point, **exactly one** handler — two bindings on one command channel fail the build; no answer; its outcome is a verdict |

The two kinds carry different risk, and the rules follow it:

- **An event is heard in either exposure, with nothing to declare.** It reacts to what already
  happened: the PRD_14 fallback access rule does not apply to it (the bus's own guard still runs
  on the delivery). The default is the common case — a project whose queues only listen writes no
  decorator.
- **A Command is never consumed without a declaration, in either exposure**, and it says who may
  send it (`producers=`, `@auth.*`, or `unguarded=True`) — a Command on a queue is what an outsider
  makes this process do, the queue's version of an unintended public API.
- **Contexts of this process hearing one event in one consumer group are one subscription** that
  runs each of them — two subscriptions on one RabbitMQ queue would compete for the message. Each
  context gets the event as the class *it* declared for the wire name. The message is acked only
  when every context ran; the inbox keys each context apart, so a retry re-runs only the one that
  failed (the verdicts combine worst first: retry, dead letter, run).

**RabbitMQ, until phase 2 — documented, not built.** A consumer group is native on Kafka
(`group_id`) and NATS (`queue=`); on RabbitMQ it needs a topology the framework does not declare
yet: the publisher sends to the default exchange with the channel as routing key, and every group
subscribes the queue named after the channel. So **two services hearing one event on RabbitMQ
compete for it** — each gets part of the events. One service per event, or the replicas of one
service, work as specified. For several services on one event today, declare the topology with
`QueueOptions(subscription_of=...)` (a queue per group bound to an exchange) and publish to that
exchange. Phase 2 makes it the default.

#### 5.2 Reliability — normative

1. **Acknowledgement is always explicit**: `AckPolicy.MANUAL`, settled by the gateway from the
   verdict. The built-in defaults (`ACK_FIRST` on Kafka, `REJECT_ON_ERROR` elsewhere) **MUST NOT**
   be relied on. *This fixes today's at-most-once behaviour; `brokers.md` is corrected with it.*
2. **At least once, plus an inbox**: before the bus, the delivery is claimed as
   `inbox:{binding}:{source}:{id}` in `IdempotencyRecords`; a redelivery of a completed message is
   acknowledged without running. On records that commit in the write's transaction, effects are
   exactly once within that database.
3. The **inbox** keys the delivery (CloudEvents `source` + `id`); `@idempotency.once` keys the
   business request (`idempotency_key()`). Both use the same port, as two stages: the inbox in the
   wire, `once` in the bus pipeline.
4. **Verdicts**, by failure kind (§1.3): ack; retry with delay; dead-letter now; ack-and-skip for an
   event this consumer does not handle on a shared channel. Retries stop at `max_attempts` (the
   broker's delivery count, or an attempts header), then dead-letter with the reason in headers.
5. The handler's time limit **MUST** stay below the broker's visibility timeout and the inbox's
   `in_progress_for`. Order holds per key only, and only with a concurrency of one per key. A message
   past its TTL is dead-lettered.

#### 5.3 Envelope, identity, document

- **CloudEvents 1.0, binary mode**: `id` (the event's UUID v7), `type` (the wire name), `source`
  (the service's URN), `subject` (the entity), `time`, `traceparent` in the protocol headers,
  `correlationid` / `causationid` as extensions.
- **Identity is the producer's service**, never a user credential (CloudEvents `authcontext` is
  informational only): RabbitMQ's validated `user-id`, a connection principal, or a signed envelope
  across trust boundaries. `@auth.requires` works against that service identity; a Command's binding
  declares `producers=` or `@auth.public` — the PRD_14 fallback rule, which events are exempt from
  (§5.1).
- **AsyncAPI 3.0**, generated by FastStream, with `receive` operations from the bindings.

#### 5.4 Full control

```python
@broker.subscriber("legacy.topic", ack_policy=AckPolicy.MANUAL)
async def legacy(message: StreamMessage) -> None:
    await gateway.consume(message, as_=CommandIssueInvoice, decode=legacy_decoder)
```

**Status — queues on FastStream, phase 1: built** (`sincpro_framework.entrypoints.faststream`,
`[faststream]` extra; `docs/entrypoints/queue.md`). `QueueGateway` (`wire = "queue"`, its port
`QueueWire`; `exposure=`, `unguarded=`, `port=` forwarded to the core): every registered
DomainEvent is heard (the core's `_published_by_default`), `@queue.hears` only moving its group or
channel; Commands are consumed only when declared, in either exposure; a group's `prefix`
prefixes channels, its `namespace` (else the alias) is the consumer group; several contexts on
one event in one group share one subscription, each handed its own class, verdicts combined worst
first. Refused at build: two handlers on one command channel, a command channel carrying events,
`max_attempts`/`concurrency` < 1, `time_limit` ≥ `in_progress_for`, and — for Commands only — the
fallback rule (`producers=` or `@auth.*`, or `unguarded=True`). `subscribe()` is the shortcut over
it (`events_gateway`): the subscriber's events only, one consumer group, a Command never consumed. Every subscription `AckPolicy.MANUAL`, settled from a verdict (ack / replay /
skip / retry / dead-letter, §1.3's queue column); attempts from `x-delivery-count` (RabbitMQ
quorum), JetStream's count, or the `sincpro-attempts` header; retry by nack (RabbitMQ, NATS) or a
counted copy (Kafka, Redis); dead letter by reject (RabbitMQ's DLX) or a copy on `{channel}.dlq`
with the reason, kind, origin and attempts in headers — a copy that cannot be published nacks.
Inbox `inbox:{context.Command}:{source}:{id}` in `IdempotencyRecords` (in memory by default,
configurable, `None` off). CloudEvents binary mode (`ce_*` written; `ce_`, `ce-`, `cloudEvents:`,
`cloudEvents_` read); `producers=` checked against `ce_source` (or `rabbit_user_id`) before
decoding; `expires_after`; `time_limit`; the bus in a worker thread inside the producer's trace,
authenticated from the message headers in that thread. `gateway.consume(message, as_=, decode=)`
with a parity test; `gateway.asyncapi()` from FastStream's generator. Tested on
`TestKafkaBroker`, `TestRabbitBroker`, `TestRedisBroker`, and checked once by hand against real
RabbitMQ 3.13 (quorum queue: nack, redelivered, rejected at the third attempt) and Redpanda (two
counted copies, then `.dlq` with the reason). **Not built**: delayed retries; the OTel
messaging `process` span and link mode (the bus adopts the producer's trace as parent);
per-Command payload schemas in the AsyncAPI document and its meta-schema validation; real-broker
tests in CI; signed envelopes; RabbitMQ fan-out per consumer group needs an exchange declared via
`subscription_of`.

### 6. Conformance — what the release MUST prove

| Wire | Tests |
|---|---|
| every wire | the §1.2 parity test; one test per failure kind → its encoding; a manifest snapshot; the PRD_14 build refusals; access and idempotency through the wire (not only through the bus) |
| REST | the OpenAPI document validates (`openapi-spec-validator`), `$ref`s resolve, `operationId`s unique; Schemathesis against the ASGI app (responses match the schema, no 5xx for valid input); the DTO validated exactly once per request; 201 + `Location`, 204 empty; concurrent POSTs with one `Idempotency-Key` run once; the contextvar regression (identity survives to the bus) |
| gRPC | reflection v1 and v1alpha list every method; `google.rpc.Status` details per kind, `INTERNAL` without the exception; an expired deadline never runs the Feature; auth via metadata; idempotency via metadata; backpressure answers `RESOURCE_EXHAUSTED`; health flips on shutdown; typed: lock drift fails, deleted field reserved, `int64`/`Decimal`/`datetime`/`bytes` round-trip exactly |
| JSON-RPC | every example of the JSON-RPC 2.0 specification as a golden test (batches, notifications, null id); an all-notification batch answers 204; a batch over the limit answers one error; the OpenRPC document validates against the meta-schema; a name is unchanged when a Feature becomes an ApplicationService |
| Queue | on `TestKafkaBroker`, `TestRabbitBroker`, `TestRedisBroker`, and on real Kafka and Rabbit in CI: a raising handler is redelivered on every broker (the at-most-once regression); a redelivered message runs once; each kind → its verdict; poison reaches the dead-letter queue with its reason; a producer not allowed is refused before the bus; the trace crosses the broker; Commands are never auto-subscribed; AsyncAPI validates |

### 7. Known issues this release fixes

Found while researching; each **MUST** be fixed with a regression test, whatever else ships.

| # | Issue | Fix |
|---|---|---|
| 1 | **Queues lose messages**: FastStream's default ack is `ACK_FIRST` on Kafka (the offset is committed before the handler runs) and `REJECT_ON_ERROR` on Rabbit/Redis (a failed message is discarded) — at-most-once, while `brokers.md` promises at-least-once | **fixed**: `subscribe()` settles manually — ack when handled, nack when a bus raised, reject (logged) when the payload cannot be rebuilt, ack when no bus here registered the event. Proven on the test brokers and on real RabbitMQ and Kafka: before, a failed event was delivered once and lost; now it is redelivered. `brokers.md` corrected. Redis Pub/Sub and core NATS stay at most once (they have no acknowledgement) — documented |
| 2 | The two doors are coupled both ways: `GrpcGateway` always mounts remote execution's `/sincpro.Contexts/Execute` on the public server, and `bus.serve()` builds its host **from** a `GrpcGateway`, so hosting a context internally also publishes its whole catalog as public `Struct` methods with reflection; `remote_execution` imports `entrypoints.grpc.wire` | **fixed**: the `transport` core (`transport.grpc`, `transport.failures`); `bus.serve()` builds a server of its own with the open host and its health only — the catalog's methods, introspection and reflection are not there; `GrpcGateway` serves its surface only; `open_host([...]).mount(server)` puts both on one port on purpose; `tests/test_package_layers.py` fails if either door imports the other, lazily included; `tests/remote_execution/test_two_doors.py` proves each door. Breaking: a context map pointing `grpc://` at a `GrpcGateway` port now needs that port to mount the door |
| 3 | gRPC ignores deadlines — a call whose caller gave up still runs | the deadline pre-check (§3.3) |
| 4 | gRPC has no backpressure — calls queue silently behind the worker pool | `maximum_concurrent_rpcs` (§3.3) |
| 5 | gRPC reflection is v1alpha only; errors travel as text | reflection v1 + v1alpha; `google.rpc.Status` details (§3.3) |
| 6 | JSON-RPC batches have no size limit | batch and body limits (§4) |
| 7 | `grpc.md` states every `DomainError` is `FAILED_PRECONDITION`; the code answers `ABORTED`/`ALREADY_EXISTS` for conflicts; `rpc.md` claims JSON-RPC 2.0 allows the body's `context` member, which the specification does not say | docs corrected to the code and the specification |

### 8. Release phases

| Phase | Contents |
|---|---|
| **1 — the release** | §0 (remote execution out of the entrypoint gateways) and §1 in full; the known issues of §7; REST on FastAPI with the `resource` profile by default and the `rpc` profile on request, the bindings, problem details, `Idempotency-Key`, listings as a `Query` with `Criteria`, the full-control exports; gRPC `struct` with rich errors, deadlines, backpressure, reflection v1, health with the drain on SIGTERM, metadata, AIP naming only, `bus_call`; JSON-RPC names without the layer, limits, the error catalogue, the validated document, `dispatch`; queues with explicit settling, the verdicts, `@queue.consumes`/`@queue.hears`, the key-value inbox, dead letters, CloudEvents, AsyncAPI, `consume`; every test of §6; `brokers.md`, `grpc.md`, `rpc.md`, `rest.md` corrected |
| **2 — minors** | gRPC health per service (one readiness per bus); gRPC `typed` with the lock and `buf`; `recommended_service_config`; `grpc.aio`; REST `ETag`/`If-Match`, merge patch, deprecation headers, CORS recipe; JSON-RPC `_meta`, idempotency, examples; queue RabbitMQ fan-out per consumer group (an exchange per channel, a quorum queue `{channel}.{group}` per group, the publisher on the exchange); queue transactional inbox and outbox relay, `Fault` events, delayed retry topics, signed envelopes, OTel link mode |
| **3 — later** | REST HTTP caching and long-running operations; gRPC streaming, Envoy transcoding, `google.longrunning`; JSON-RPC over WebSocket with event subscriptions; request-reply over queues; a dead-letter replay tool |

## Roadmap — from the application layer outward

The order the framework grows in, each step resting on the one before:

1. **Application layer** — use cases, the bus, DTOs. *Built.*
2. **Non-functional components on the use case** — access (PRD_11), idempotency and caching
   (PRD_13), observability. *Built, PRD_13 phase 2 before release.*
3. **What a use case exposes** — declared exposure (PRD_14), the syntax this release introduces:
   `@rest.post(...)`, `@grpc()`, `@rpc()`, `@mcp()`, `@queue.consumes(...)`. *Specified.*
4. **How each protocol carries it** — this PRD, on each protocol's mature host. *Specified.*
5. **Contracts outside the process** — typed gRPC with a lock, OpenAPI/OpenRPC/AsyncAPI snapshots
   and breaking-change checks in CI. *Phase 2.*
6. **The edge** — gateways and transcoding (Envoy), realtime (SSE/WebSocket), streaming,
   long-running operations. *Phase 3.*

After this release the syntax is stable: new capabilities arrive as **minor** versions (a new binding
field, a new wire, a new strategy) and fixes as **patches**; a change to a published name or shape is
a new major.

Beside this roadmap, and not part of it: **remote execution** (§0) keeps its own track — service
identity on every call, and gRPC as the recommended transport.

## Decisions

- **Marry the host, do not rebuild it.** FastAPI, grpcio, the JSON-RPC and OpenRPC specifications,
  FastStream: each already solves its protocol, and each is what a project would reach for; the
  framework adds the bus, the components and the derivation, never a second web framework.
- **FastAPI over the Starlette emitter**: PRD_12's objection — a second validation — disappears when
  the body is the DTO and the bus is called directly; what decides it is that only one generator can
  produce one OpenAPI document for generated and hand-written routes together.
- **Struct stays gRPC's default; typed is opt-in with a lock**: the DTO has no field numbers, so
  numbers become committed, reviewed state rather than derived from field order.
- **No layer in any public name**: a Feature promoted to an ApplicationService is an internal change;
  REST already kept the layer out, and gRPC and JSON-RPC follow.
- **Remote execution and entrypoints are two doors**: one keeps a codebase whole across machines,
  the other publishes a contract to others; sharing a server made the internal door public by
  accident, so they are served apart.
- **Resource by default, new names without a transition**: breaking changes are acceptable in this
  release, so REST starts resource-shaped and gRPC/JSON-RPC start with their final names; everything
  after this release lands as minors (features) and patches (fixes).
- **Queues are an entrypoint, not only events**: consuming a Command is exposure — it needs the same
  opt-in, identity, fallback rule and manifest as any other wire.

## Open questions

1. **The queue inbox by default.** On for every consumed Command (safe, one store round trip per
   message), or opt-in per binding?
2. **Remote execution's default transport.** `http://` needs no extra and is what a caller reaches
   with the standard library; `grpc://` is recommended. Keep the address deciding (today), or have
   `bus.serve()` document HTTP as the fallback and gRPC as the default host (today it is gRPC)?
