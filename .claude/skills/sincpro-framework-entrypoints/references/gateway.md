# The shared gateway

Every wire extends `entrypoints.Gateway`, so exposing buses is learned once. Full depth:
`docs/entrypoints/integration.md`.

## Automatic vs narrowed

```python
RestGateway([billing, sales])            # every use case of every bus
gateway.add(bus, include=[CommandA, "CommandB"])
gateway.add(bus, exclude=[CommandReconcile])
gateway.add("ventas", sales)             # an alias of the caller's
gateway.add(bus, wrap={CommandIssue: audit})
Gateway(..., layers=("app_services",))
```

`include`/`exclude`/`wrap` take the Command class or its name; a name no use case answers is warned
about (in `exclude` it would leave exposed what it meant to hide).

`@internal` (from `sincpro_framework.entrypoints`) keeps a use case off **every** wire, now or
later. It runs only in the process.

## Declared exposure

The catalog publishes by forgetting; a public API declares. See the SKILL for the decorators. The
binding types are frozen DTOs:

| Decorator | Binding | Fields (`None` = derive) |
|---|---|---|
| `rest.get/post/put/patch/delete(path, ...)` | `RestBinding` | `method`, `path`, `status`, `location`, `tags`, `summary`, `responses`, `body` (`"json"`/`"merge-patch"`), `concurrency` (`"if-match"`) |
| `rpc(name=None, notification=False)` | `RpcBinding` | `name`, `notification` |
| `grpc(service=None, method=None)` | `GrpcBinding` | `service`, `method` |
| `mcp(name=None, title=None, read_only=None, destructive=None, open_world=False)` | `McpBinding` | `name`, `title`, `read_only`, `destructive`, `open_world` |
| `queue.consumes(channel, producers=, max_attempts=, concurrency=)` / `queue.hears(group=)` | `QueueBinding` | `kind`, `channel`, `producers`, `max_attempts`, `concurrency`, `group` |

Every one takes `deprecated=True` or `deprecated=Deprecation(since=, sunset=, replacement=)`.

- Bindings are kept **by class, never on the class**; the order the decorators are written never
  matters.
- **One binding per wire** per class; a second is refused where written (`ExposureRefused`).
- A `replaces=` handler inherits the binding of the one it replaces, wire by wire.
- A project's own binding: subclass `Binding` with its own `wire`, `declare(handler, binding)`.

**Modes.** `Exposure.DECLARED` (default for a gateway that names its wire) publishes only the bound
use cases; `Exposure.CATALOG` publishes every one, logging each. A group gives a context its
conventions on this wire:

```python
api.group(billing, prefix="/billing", tags=("billing",), version="v1")
api.override(CommandIssueInvoice, status=202)          # shape only, never publishes
api.bind(QueryInvoice, RestBinding(method="GET", path="/invoices/{invoice_id}"))   # publishes
api.add(billing, exclude=[CommandReconcile])
```

## Precedence (highest first)

1. `@internal` — absolute.
2. `exclude` / `include` (in DECLARED, `include` only narrows).
3. `override` / `bind` — field-level merge, last call wins.
4. The decorator on the handler.
5. The group's conventions.
6. What the wire derives.

## Build fails closed

`surface()`, `build()`, `manifest()`, `operations()` validate first and raise `ExposureRefused` with
**every** reason at once; `verify()` answers the list without raising. Refused, among others:
`@internal` with a binding; a path `{field}` that is not a Command field; a `location` field not a
response field; `GET` on a non-`Query`; a hint against a fact (`mcp(read_only=True)` on a Command);
a reserved name; a deprecation past its sunset; a published use case on a guarded bus that declares
no access.

## Inventory and the facade

```python
def test_the_public_surface_does_not_move():
    assert [entry.model_dump(mode="json") for entry in api.manifest()] == SNAPSHOT
```

`manifest()` is a tuple of frozen, JSON-safe `ManifestEntry`, sorted. The gateway never starts
anything: it hands back what it builds — `routes()`, `app()`, `server()`, `handlers()` — for the
project to mount, add middleware to, or serve.

## A wire of the project's

Subclass `Wire[MyBinding]` (`binding`, `derive`, `name_of`, `validate`, `build`) and pass
`Gateway(..., port=MyWire())`. Each `Resolved` carries its `Group`, binding and `Operation` — and
`run`, which calls the bus (guards and stages run for a project's wire too).

## Failures

One classification, each wire's code: `entrypoints.errors.failure_kind(error)`.

| Kind | Raised | REST | JSON-RPC | gRPC |
|---|---|---|---|---|
| invalid | DTO did not validate | 422 (400 unreadable) | `-32602` | `INVALID_ARGUMENT` |
| unauthenticated | `Unauthenticated` | 401 + `WWW-Authenticate` | `-32001` | `UNAUTHENTICATED` |
| permission_denied | `PermissionDenied` | 403 | `-32003` | `PERMISSION_DENIED` |
| conflict | `StaleAggregate`, `DuplicateAggregate` | 409 | `-32009` | `ABORTED`/`ALREADY_EXISTS` |
| domain | any other `DomainError` | 422 | `-32010` | `FAILED_PRECONDITION` |
| internal | anything else | 500 (nothing told) | `-32603` | `INTERNAL` |

## Practices

- Gateways last (a built bus takes nothing more).
- Explicit aliases for a public wire.
- What the client sends is the client's; the framework validates the DTO and never takes identity
  from the context. A use case that decides on a context value validates it.
