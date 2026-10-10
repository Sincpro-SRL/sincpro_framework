# Integrating buses with an entrypoint — one way, on every wire

Every entrypoint — JSON-RPC, gRPC, MCP, REST — extends one base, `entrypoints.Gateway`, so
exposing buses is learned once and reads the same on every wire. Two ways to say what is
published: the **catalog** (every use case, narrowed) and **declared exposure** (only what a use
case binds to a wire — [below](#declared-the-use-case-says-on-which-wire-and-how-prd_14)).

## Automatic: give it the buses

```python
RestGateway([billing, sales])
RpcGateway([billing, sales])
GrpcGateway([billing, sales])
McpGateway([billing, sales]).server()
```

Every Feature and ApplicationService of every bus is published, each bus under its own name made
fit for the wire: `sincpro-billing` is the REST and JSON-RPC alias as it is, and the gRPC package
`sincpro_billing` — a proto package allows no hyphen. `{"billing": billing}` names the alias
instead.

## Narrowed: say what not to publish

| Want | How | Scope |
|---|---|---|
| A use case on **no** wire, ever — run only in the process | `@internal` on the handler or its Command (`from sincpro_framework.entrypoints import internal`) | every gateway, added now or later |
| Only some use cases of a bus on one wire | `gateway.add(bus, include=[CommandA, "CommandB"])` | that gateway |
| All but some | `gateway.add(bus, exclude=[CommandReconcile])` | that gateway |
| One layer only | `Gateway(..., layers=("app_services",))` | that gateway |
| An alias of the caller's | `gateway.add("ventas", sales)` | that gateway |
| A run decorated — audit, extra logging | `gateway.add(bus, wrap={CommandIssue: audit})` | that gateway |
| REST: a path of the project's | `rest.route(QueryInvoice, "GET /billing/invoices/{invoice_id}")` | that gateway |

`include`, `exclude` and `wrap` take the Command class or its name; a name no use case answers is
warned about, since in `exclude` it would leave exposed what it meant to hide.

## Declared: the use case says on which wire, and how (PRD_14)

The catalog above publishes by forgetting: a new use case is public the moment it is registered.
A public API declares instead. The decorators live in `sincpro_framework.entrypoints`
and import no transport library, so a `services/` module stays free of Starlette, grpc and
FastMCP:

```python
from sincpro_framework.entrypoints.entrypoint.decorators import grpc, mcp, queue, rest, rpc

@billing.feature(CommandIssueInvoice)
@auth.requires(BillingPermission.ISSUE)
@idempotency.once(expires_after=timedelta(hours=24))
@rest.post("/invoices", status=201, location="/invoices/{number}")
@rpc()                                   # name derived
@grpc()                                  # service and method derived
@mcp(title="Emitir factura", destructive=False)
class IssueInvoice(Feature): ...

@billing.feature(QueryInvoice)
@auth.requires(BillingPermission.READ)
@rest.get("/invoices/{invoice_id}")      # {invoice_id} must be a field of QueryInvoice
@mcp()                                   # read-only: it is a Query
class GetInvoice(Feature): ...
```

| Decorator | Binding (a frozen DTO) | Fields — `None` means *derive it* |
|---|---|---|
| `rest.get/post/put/patch/delete(path, ...)`, `rest(path, method=None, ...)` | `RestBinding` | `method`, `path`, `status`, `location`, `tags`, `summary`, `responses`, `body` (`"json"` / `"merge-patch"`), `concurrency` (`"if-match"`) |
| `rpc(name=None, notification=False)` | `RpcBinding` | `name`, `notification` |
| `grpc(service=None, method=None)` | `GrpcBinding` | `service`, `method` |
| `mcp(name=None, title=None, read_only=None, destructive=None, open_world=False)` | `McpBinding` | `name`, `title`, `read_only`, `destructive`, `open_world` |
| `queue.consumes(channel, producers=, max_attempts=, concurrency=)` · `queue.hears(group=)` | `QueueBinding` | `kind`, `channel`, `producers`, `max_attempts`, `concurrency`, `group` |

Every one takes `deprecated=True` or `deprecated=Deprecation(since=, sunset=, replacement=)`.

- A decorator records its binding in a registry kept **by class, never on the class**, and
  returns the class unchanged — the order the lines are written in never matters. It records only
  what it was told: a field left at its default stays unset, so it never overrides a group.
- **One binding per wire** per class: a second `@rest...` on the same class is refused where it
  is written (`ExposureRefused`).
- A `replaces=` handler inherits, **wire by wire**, the binding of the one it replaces when it
  declares none for that wire: replacing a handler never moves or drops a public operation.
- A project's own binding type (a subclass of `Binding` with its own `wire`) is recorded the same
  way: `declare(handler, binding)`.
- `@internal` stays absolute: a class that is `@internal` and bound fails the build.

### Modes

A gateway that **names its wire** resolves a surface:

| Mode | Publishes |
|---|---|
| `Exposure.DECLARED` — the default | only the use cases with a binding for this wire |
| `Exposure.CATALOG` | every use case of every bus, each one logged at build; a binding still shapes it, never adds one |

```python
Gateway(buses, exposure=Exposure.DECLARED, unguarded=False, port=None)
```

A wire's gateway names its wire with a class attribute (`wire = "rest"`), or a project hands a
`Wire` port (below). **The shipped wires (REST, JSON-RPC, gRPC, MCP) do not name theirs yet**:
until each opts in, it publishes its catalog exactly as the sections above say, and
`operations()` answers what it always did.

### Groups — one per bounded context

```python
api.group(billing, prefix="/billing", tags=("billing",), version="v1")
api.group(sales, prefix="/ventas")
```

`group(bus_or_alias, prefix=, tags=, version=, namespace=, package=)` records the conventions of
one context on this wire (`gateway.groups`, `Resolved.group`); a field left `None` is the wire's
default. A group for a bus the gateway does not serve is refused.

### The composition's word, and precedence

```python
api.override(CommandIssueInvoice, status=202)                                     # shape
api.bind(QueryInvoice, RestBinding(method="GET", path="/invoices/{invoice_id}"))  # publish
api.add(billing, exclude=[CommandReconcile])                                      # take away
```

`override` and `bind` take the Command or its handler, and are checked against the gateway's
binding type where they are written — `override(..., stauts=201)` and a `bind` of another wire's
binding are refused. `override` shapes a binding and never publishes: in DECLARED mode an
override alone is refused at build. `bind` publishes.

Highest first:

1. `@internal` — absolute.
2. `exclude` / `include` — in DECLARED mode `include` only narrows.
3. `override` / `bind` — a field-level merge; the last call wins.
4. The decorator on the handler.
5. The group's conventions.
6. What the wire derives from the facts.

A scalar declared by a closer layer wins; a collection (`tags`) declared adds to the derived one.

### Validation — the build fails closed

`gateway.surface()`, `build()`, `manifest()` and `operations()` (on a gateway that names its
wire) validate first and raise `ExposureRefused` with **every** reason at once;
`gateway.verify()` answers the list without raising.

| Refused | Example |
|---|---|
| `@internal` with a binding | `@internal` + `@rest.post(...)`, or `bind` on an internal Command |
| an override or bind for a Command no bus of the gateway answers | |
| an override alone, in DECLARED mode | `override(QueryX, status=200)` with no REST binding |
| a published use case that declares no access | neither `@auth.requires`, `@auth.public` nor `@auth.authenticated` on a guarded bus; a bus with no `AccessControl` unless the gateway says `unguarded=True` |
| a path `{field}` not a Command field | `/invoices/{id}` on a Command with `invoice_id` |
| a `location` `{field}` not a response field | |
| the method against the Command's kind | `GET` on a Command that is not a `Query`; `DELETE` or `204` with a declared response |
| a `PATCH` with required fields and no `body="merge-patch"` | |
| a hint against a fact | `mcp(read_only=True)` on a Command; `read_only=False` on a Query |
| a reserved name | an RPC name starting with `rpc.` |
| a deprecation past its sunset | |
| a queue binding against the message | `queue.consumes` on a `DomainEvent`; `queue.hears` on a Command |
| a stage against the Command's kind | `@idempotency.once` on a `Query` |
| a second binding for one wire on a class | refused at declaration |
| the wire's own clashes | what the `Wire` port's `validate` answers |

The facts of every wire are checked by any gateway: a REST gateway refuses an MCP hint that
contradicts the Command, because the declaration is wrong whatever wire builds it.

### Inventory

```python
def test_the_public_surface_does_not_move():
    assert [entry.model_dump(mode="json") for entry in api.manifest()] == SNAPSHOT
```

`gateway.manifest()` answers a tuple of frozen, JSON-safe `ManifestEntry` — wire, context,
Command, name, kind (`features.command`, `app_services.query`), method, access, deprecation, the
resolved binding — sorted, so a snapshot only moves when the surface does. CATALOG mode logs the
same entries at build.

### A wire of the project's — the `Wire` port

```python
class CliBinding(Binding):
    wire = "cli"
    command: str | None = None

class CliWire(Wire[CliBinding]):
    binding = CliBinding

    def derive(self, operation: Operation, group: Group) -> CliBinding:
        return CliBinding(command=f"{group.prefix or group.alias} {operation.command.__name__}")

    def name_of(self, resolved: Resolved[CliBinding]) -> str:     # optional: its manifest name
        return resolved.binding.command or ""

    def validate(self, surface: Sequence[Resolved[CliBinding]]) -> list[str]:
        return []                                                   # its own clashes

    def build(self, surface: Sequence[Resolved[CliBinding]]) -> Cli:
        return Cli({one.binding.command: one.operation.run for one in surface})

cli = Gateway([billing], port=CliWire()).group(billing, prefix="fact").build()
```

The gateway resolves (precedence), validates (every rule above, then `validate`) and hands the
port the surface. Each `Resolved` carries its `Group`, its binding and its `Operation` — context,
bus, Command, handler, schemas, `is_query`, `idempotent`, `access`, and `run`, which calls the
bus: the access guard and every stage run for the project's wire too.

## The facade: what it builds is handed back

A gateway never starts anything by itself. What it builds is returned, so the project adds its
middleware, interceptors and plugins:

| Wire | Returned | Where the project extends it |
|---|---|---|
| REST | `rest.routes()` · `rest.app(middleware=, routes=, lifespan=)` · `rest.openapi()` | Starlette middleware (CORS, `IdentityMiddleware`, rate limits), mounting into FastAPI, extra routes |
| JSON-RPC | `rpc.routes()` · `rpc.app(...)` · `rpc.discover()` | the same |
| gRPC | `grpc.handlers()` · `grpc.server(interceptors=, options=)` · `grpc.proto_files()` | `grpc.ServerInterceptor`s, its own `grpc.Server`, TLS credentials |
| MCP | `mcp.server(auth=, base_url=)` — a FastMCP instance | FastMCP middleware, prompts, resources |

`.run(...)` on each is the one-liner for a service that serves only that wire.

## Practices

- **A public wire gets explicit aliases.** The automatic mode publishes each bus under its own
  name — the name its logs and error tracker use too. For a wire third parties or agents build
  on, name the alias yourself (`add("billing", bus)`): renaming the bus then never moves a URL, a
  gRPC package or a tool.
- **Gateways are made last.** Adding a bus to a gateway builds it, and a built bus takes nothing
  more — so `auth.on(bus)`, the interceptors and the handlers are registered first, the gateways
  at the end of the composition. Done the other way, `BusAlreadyBuilt` says what came too late.
- **What the client sends is the client's.** JSON-RPC opens the `context` a request carries, and
  every wire opens the DTO's fields as they came; the framework validates the DTO and never takes
  identity from either. A use case that decides on a context value validates it.

## Failures — one kind, each wire's code

| Kind | Raised | REST | JSON-RPC | gRPC | Told to the caller |
|---|---|---|---|---|---|
| `invalid` | the DTO did not validate | 422 (400 if unreadable) | `-32602` | `INVALID_ARGUMENT` | the validation errors |
| `unauthenticated` | `Unauthenticated` | 401 + `WWW-Authenticate` | `-32001` (a lone call: HTTP 401) | `UNAUTHENTICATED` | the reason, `step_up` |
| `permission_denied` | `PermissionDenied` | 403 | `-32003` | `PERMISSION_DENIED` | the reason, the requirement |
| `not_found` | an error declaring `failure_kind = FailureKind.NOT_FOUND` | 404 | `-32004` | `NOT_FOUND` | the message |
| `conflict` | `StaleAggregate`, `DuplicateAggregate` | 409 | `-32009` | `ABORTED` / `ALREADY_EXISTS` | the message — read again and retry |
| `in_progress` | `caching.AlreadyInProgress` | 409 | `-32029` | `ABORTED` | retry once it completes |
| `key_reused` | `caching.KeyReused` | 422 | `-32022` | `FAILED_PRECONDITION` | a new request needs a new key |
| `domain` | any other `DomainError` | 422 | `-32010` | `FAILED_PRECONDITION` | the message |
| `exhausted` | an error declaring `FailureKind.EXHAUSTED` | 429 | `-32029` | `RESOURCE_EXHAUSTED` | retry later |
| `unavailable` | an error declaring `FailureKind.UNAVAILABLE` | 503 | `-32000` | `UNAVAILABLE` | retry later |
| `internal` | anything else | 500 | `-32603` | `INTERNAL` | nothing — it stays in the log |

`common.failures.refined_failure_kind(error)` is the one classification — the wires and the
`ExecutionFailed` event share it; a wire only chooses its code.

## What every wire does the same

- **Auth**: a bus guarded by an `AccessControl` is authenticated on every wire, and refused the
  way each protocol's clients understand — see [auth](../auth/README.md#entrypoints-authenticate-by-themselves).
- **Validation**: the DTO validates the payload; a refusal is the wire's "invalid" answer.
- **Disclosure**: a `DomainError`'s message reaches the caller; anything else stays in the log.
- **Health**: `gateway.is_healthy()` — every bus still built — behind each wire's health check.
- **Optional**: a wire's library (`starlette`, `grpc`, `fastmcp`) is read only when that wire is
  served; building a gateway, its route table or its document needs none of them.
