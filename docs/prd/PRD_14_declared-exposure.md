# PRD_14: Declared exposure — a use case says on which wire it is published, and how

- **Status**: the core is built — `entrypoints/exposure` (registry, bindings, decorators,
  `Exposure`, `Group`, `Deprecation`, the `Wire` port) and the resolution, precedence, build
  validation and manifest on `Gateway` (§11 says piece by piece). Every wire consumes it:
  `FastApiGateway` (rest), `GrpcGateway`, `RpcGateway`, `McpGateway` and `QueueGateway` set their
  `wire` and are DECLARED by default; the frozen Starlette `RestGateway` still publishes PRD_12's
  catalog. How each wire carries the surface — REST on FastAPI, gRPC on grpcio, JSON-RPC, queues
  on FastStream — is [PRD_15](PRD_15_entrypoints-on-mature-hosts.md). Kept from PRD_12: catalog
  mode (`Gateway([bus])`, `include`/`exclude`, `@internal`, `rest.route(Dto, "GET /…")`).
- **Depends on**: PRD_12 (`Gateway`, `Catalog`, the four wires), PRD_11 (`AccessControl`,
  `@auth.requires`, `@auth.public`), PRD_13 (`@idempotency.once`).
- **Shapes**: bindings are frozen DTOs; the registry keeps them by class, never on the class; a
  wire is an abstract class a new transport implements.
- **Philosophy**: one metadata model, many writers. A decorator on the use case records what it
  wants published; the composition adds, overrides or narrows it; each wire translates it into its
  own concepts and builds; a build step validates the whole surface and fails closed. Everything
  the framework can prove is derived; only intent it cannot infer is declared.

## Problem

PRD_12 publishes by catalog: `RestGateway([billing, sales])` puts every use case of every bus on
the wire, each named by a rule of that wire, and what must not be public is taken away —
`@internal`, `exclude`, `layers`. That is the right default for an SDK or an internal RPC surface,
and the wrong one for a public API:

- **Exposure is opt-out.** A new use case is public the moment it is registered; forgetting
  `@internal` publishes it (OWASP API9, improper inventory).
- **The wire shape is not the use case's to say.** A REST resource path, a `201`, a gRPC method
  name, an MCP tool that does not destroy anything — only `rest.route` for one REST path exists,
  at the composition, keyed by name.
- **Each protocol has its own concepts** — a route group, a package, a namespace, a tool — and
  there is no place to declare them once per bounded context.

## Background — what mature frameworks settled on

| Framework | Where it is declared | What this spec takes |
|---|---|---|
| **Wolverine.Http** | `[WolverinePost("/x")]` on the bus handler; `IHttpPolicy` alters every endpoint at bootstrap | a declaration on the handler, a composition pass over the same model |
| **FastEndpoints** | `Post("/x")` in the endpoint; "secure by default, you'd have to explicitly call `AllowAnonymous()`" | the fallback access rule |
| **ASP.NET Minimal APIs** | `MapPost` + `MapGroup` conventions; metadata last-wins, groups before endpoints | groups per bounded context; precedence |
| **NestJS** | `@Get`, `@GrpcMethod(service?, method?)`, `@MessagePattern` on one handler; hybrid apps skip global guards unless told — a trap | one handler on several wires; guards never bypassed |
| **Encore** | `api({expose, auth, method, path})`; `expose` false by default; exposure and auth are two axes | opt-in publication; two axes |
| **FastAPI / Litestar** | router decorators; `include_router(prefix, tags, dependencies)`; the closest layer wins | group conventions, closest-wins |
| **protobuf-net.Grpc** | `[ServiceContract]`; a `ServiceBinder` decides inclusion and names | the wire decides its names |
| **oRPC** | `.route({method, path, successStatus, tags, deprecated})`; router prefixes concatenate | the REST binding's fields |
| **MCP SDK** | `@mcp.tool(name, title, annotations)`; hints are untrusted — "servers MUST implement proper access controls" | derived hints, never authorization |
| **Google AIP-185; ASP.NET API Versioning; RFC 9745 / 8594** | the version in the package or group; `deprecated`; `Deprecation` / `Sunset` headers | versions per group, deprecation per binding |

The convergence: **decorators record declarations in a metadata store; the composition root adds,
overrides or removes them; a build step materializes and validates the final surface.**

## The specification

**MUST**, **MUST NOT**, **SHOULD**, **MAY** as in RFC 2119.

### 1. Terms

| Term | Meaning |
|---|---|
| **wire** | a transport the framework publishes on: REST, JSON-RPC, gRPC, MCP — or one a project adds (§9) |
| **binding** | what one use case declares for one wire — a frozen DTO (§4) |
| **registry** | the bindings, by handler class — written by decorators and by the composition |
| **group** | the conventions of one bounded context on one wire — prefix, package, tags, version (§5) |
| **surface** | what a gateway publishes, once every binding is resolved (§7) |
| **manifest** | the surface as data, for an inventory and a CI snapshot (§8) |

### 2. Two modes

| Mode | Publishes | For |
|---|---|---|
| `Exposure.DECLARED` — **the default** | the use cases with a binding for this wire, and nothing else | a public API: nothing is public by forgetting |
| `Exposure.CATALOG` | every use case of every bus, named by the wire's rules — PRD_12 as it is | a quick internal surface, an admin tool, an MCP server over a whole context |

Neither mode is how the project's own services reach each other: that is **remote execution**, a
whole bounded context behind the context map, specified apart (PRD_15 §0).

- A gateway **MUST** say its mode or take the default; CATALOG **MUST** log, at build, every
  operation it publishes.
- In CATALOG mode a binding **MAY** still shape an operation (a path, a status); it never adds one.
- `@internal` is **absolute** in both modes: no binding, include or mode publishes it, and a class
  that is `@internal` and declares a binding **MUST** fail the build.

### 3. The protocol's concepts — what each wire translates a binding into

| Concept | REST | JSON-RPC | gRPC | MCP |
|---|---|---|---|---|
| bounded context (the bus) | a route group: prefix + OpenAPI tag | a namespace | a package (`billing.v1`) | a tool name prefix, only on a clash |
| one use case | an operation: method + path | a method | a method of a service | a tool |
| its name | path + `operationId` | `billing.issue_invoice` | `billing.v1.Features/IssueInvoice` | `issue_invoice` |
| read vs write | `GET` vs `POST`/`PUT`/`PATCH`/`DELETE` | — | idempotency level | `readOnlyHint` / `destructiveHint` |
| its answer | a status: 200 / 201 / 202 / 204 | `result` / `error` | a status code | content / `isError` |
| its document | OpenAPI 3.1 | OpenRPC 1.4 | reflection + `Describe` | `tools/list` |
| deprecation | `deprecated`, `Deprecation` / `Sunset` headers | `deprecated` | a comment and a newer package | a description prefix |

### 4. Bindings and their decorators

The decorators live in `sincpro_framework.entrypoints.exposure` and import no transport library —
a `services/` module stays free of Starlette, grpc and FastMCP. Each records a binding and returns
the class unchanged, so their order does not matter.

```python
from sincpro_framework.entrypoints.exposure import grpc, mcp, rest, rpc

@billing.feature(CommandIssueInvoice)
@auth.requires(BillingPermission.ISSUE)
@idempotency.once(expires_after=timedelta(hours=24))
@rest.post("/invoices", status=201, tags=("invoices",))
@rpc()                                   # derived name
@grpc()                                  # derived service and method
@mcp(title="Emitir factura", destructive=False)
class IssueInvoice(Feature): ...

@billing.feature(QueryInvoice)
@auth.requires(BillingPermission.READ)
@rest.get("/invoices/{invoice_id}")      # validated: QueryInvoice has invoice_id
@mcp()                                   # read_only derived: a Query
class GetInvoice(Feature): ...
```

| Binding | Declared fields — `None` means derive (§6) |
|---|---|
| `RestBinding` | `method`, `path` (a template with `{field}`), `status`, `tags`, `summary`, `responses` (domain errors to document), `deprecated` |
| `RpcBinding` | `name`, `notification`, `deprecated` |
| `GrpcBinding` | `service`, `method`, `deprecated` (streaming: phase 3) |
| `McpBinding` | `name`, `title`, `destructive`, `open_world`, `deprecated` |
| `Deprecation` | `since`, `sunset`, `replacement` (the DTO that replaces it) |

- `rest.get/post/put/patch/delete(path, ...)` fix the method; `rest(...)` derives it.
- A path template's `{field}` **MUST** name a field of the Command; it is the wire's concept, a
  string, and the build refuses one the DTO does not have (§7) — a rename breaks the build, never
  a request.
- One class **MAY** carry one binding per wire; a second one for the same wire **MUST** fail.

### 5. Bounded contexts — groups

Each bus is a bounded context, and on each wire its **group** carries the conventions of all its
use cases:

```python
api = RestGateway()
api.group(billing, prefix="/billing", tags=("billing",), version="v1")   # /v1/billing/...
api.group(sales, prefix="/ventas")
```

| Wire | A group is | Default |
|---|---|---|
| REST | prefix, tags, version segment | `/{alias}`, tag `alias` |
| JSON-RPC | namespace | `alias` |
| gRPC | package, with its major version (AIP-185) | `alias.v1` |
| MCP | tool prefix | none — applied only when two contexts answer the same name |

- **N bounded contexts, one gateway.** Each context declares its bindings in its own `services/`;
  the composition root publishes all of them at once — what `sincpro_synthesis` does today by
  hand, one FastAPI router per context and one endpoint per Command:

  ```python
  api = RestGateway([catalog, planning, execution, assurance, project], prefix="/api")
  api.group(planning, prefix="/planes")          # only where the default is not wanted
  ```

- The **version** of an API belongs to the group, never to a use case: a breaking change is a new
  Command, bound in a newer group, the old binding deprecated.
- Group conventions are **additive** for collections (tags) and **overridden** by the binding for
  scalars — the closest layer wins (Litestar, ASP.NET).

### 6. Derived, never declared

What the framework can prove, it derives; a declaration **MUST NOT** contradict it.

| Fact | Derived into |
|---|---|
| the Command's JSON Schema, the response's | REST body and responses, OpenRPC params and result, gRPC `Describe`, MCP `inputSchema` / `outputSchema` |
| a `Query` DTO | REST `GET` (and `POST` for a long criteria); MCP `readOnlyHint=true` |
| any other Command | REST `POST`; MCP `readOnlyHint=false` |
| `@idempotency.once` | REST documents an idempotent retry; MCP `idempotentHint=true` |
| `@auth.requires` / `@auth.public` | OpenAPI `security` and `x-sincpro-requires`; the wire's 401/403 |
| the handler's docstring | summary / description on every wire |
| the DTO's name | `operationId`, the RPC and gRPC method, the tool name |

`destructive` is the one hint the framework cannot prove: undeclared on a non-idempotent Command it
**MUST** stay the MCP spec's pessimistic default, `true`. MCP hints are never authorization.

### 7. Building the surface — precedence and validation

**Precedence**, highest first:

1. `@internal` — absolute.
2. The gateway's `exclude` / `include` — in DECLARED mode `include` only narrows.
3. The gateway's `override(Command, **fields)` and `bind(Command, Binding)` — a field-level merge,
   the last call wins.
4. The decorator on the handler.
5. The group's conventions.
6. What is derived (§6).

**The build MUST fail** — on `gateway.build()`, or when the app starts — when:

| Check | Example |
|---|---|
| two operations answer the same wire name | `POST /invoices` twice; one `operationId`, RPC method, gRPC method or tool name twice |
| a path's `{field}` is not a field of the Command | `/invoices/{id}` on a Command with `invoice_id` |
| the method contradicts the Command's kind | `GET` on a Command that is not a `Query`; `status=204` with a non-empty response |
| a hint contradicts a fact | `@mcp(read_only=True)` on a Command |
| an exposed use case declares no access | neither `@auth.requires` nor `@auth.public` on a guarded bus — the fallback rule; on a bus with no `AccessControl`, the gateway **MUST** be told `unguarded=True` |
| a reserved name | an RPC name starting with `rpc.` |
| `@internal` with a binding | |
| a deprecation past its sunset | `sunset` earlier than today — it forces the removal |
| an override or bind for a Command no bus of the gateway answers | |

A binding for a wire that no gateway of the process builds **SHOULD** warn, and **MUST** fail with
`strict=True` (the default in CI).

### 8. Inventory

`gateway.manifest()` answers the surface as a frozen DTO — per operation: wire, name, method or
kind, access declared, deprecation. A project **SHOULD** snapshot it in a test, so any change to
its public surface shows in the pull request's diff (OWASP API9's inventory control). CATALOG mode
logs the same manifest at build.

### 9. A wire of the project's

A new transport (a CLI, a queue consumer, a webhook receiver) implements the `Wire` port and gets
decorators, groups, precedence and validation for free:

```python
class Wire[B: Binding](ABC):
    binding: type[B]                                   # its binding DTO
    def derive(self, operation: Operation, group: Group) -> B: ...
    def validate(self, surface: Sequence[Resolved[B]]) -> list[str]: ...   # its own clashes
    def build(self, surface: Sequence[Resolved[B]]) -> Any: ...            # routes, a server…
```

The registry is generic — `declare(handler, binding)` — so a project's binding type is recorded by
its own decorator the same way.

### 10. One vocabulary on the use case, one order at run time

Everything a use case declares sits on its class, and the order the decorators are written in
**MUST NOT** change what happens: Python applies decorators bottom-up, and a contract that depends
on that order breaks the day someone reorders two lines.

**The vocabulary**, written by convention in this order — for reading, not for meaning:

```python
@billing.feature(CommandIssueInvoice)          # 1. registration — which bus answers it
@auth.requires(BillingPermission.ISSUE)        # 2. access — who may call it (or @auth.public)
@idempotency.once(expires_after=timedelta(hours=24))   # 3. behaviour — a write run once
@caching.keeps(ttl=timedelta(minutes=5))       #    or a read kept (a Query only)
@rest.post("/invoices", status=201)            # 4. exposure — on which wires, how
@mcp(destructive=False)
class IssueInvoice(Feature): ...
```

| Kind | Decorators | What it does to the class |
|---|---|---|
| **registration** | `@bus.feature`, `@bus.app_service` | registers it on the bus |
| **markers** | `@auth.requires`, `@auth.public`, `@internal`, every exposure binding | records data in a registry; the class is untouched |
| **stages** | `@idempotency.once`, `@caching.keeps` | records a stage of the use case's pipeline |

**The order at run time is the framework's, fixed**, whatever the order written:

```text
wire (REST · JSON-RPC · gRPC · MCP)
  → bus
    → access guard              @auth.requires — outermost: nothing runs for who may not call it
      → idempotency stage       @idempotency.once — a replay is authorized, and runs nothing below
        → caching stage         @caching.keeps — a cached answer is authorized, and runs nothing below
          → the project's interceptors, by before / after / sequence
            → execute
```

- A stage decorator **MUST** record its stage rather than wrap `execute` in place: the use case
  gets one pipeline that runs its stages by rank. Two stages written in either order run the same
  way. *Today `@idempotency.once` wraps `execute` directly (PRD_13) — it moves to a stage.*
- A stage **MUST NOT** be skipped by the wire: every wire calls the bus, never `execute`.

**Combinations the build MUST refuse:**

| Combination | Why |
|---|---|
| `@idempotency.once` on a `Query` | a read changes nothing; there is nothing to run once |
| `@caching.keeps` on a Command that is not a `Query` | a write is never answered from a cache |
| `@internal` with any exposure binding | contradictory: never published, and published |
| an exposure binding with neither `@auth.requires` nor `@auth.public` on a guarded bus | the fallback access rule (§7) |
| one stage or one binding per wire declared twice on a class | only one of them could hold |

### 11. Status per piece

| Piece | Status |
|---|---|
| CATALOG mode, `include` / `exclude`, `@internal`, `rest.route` | built (PRD_12); `rest.route` becomes `bind(Command, RestBinding(...))` when REST opts in |
| the registry, bindings, decorators (`rest`, `rpc`, `grpc`, `mcp`, `queue.consumes` / `queue.hears`), `declare` | **built** — `entrypoints/exposure`, no transport import (tested) |
| DECLARED mode (the default) and CATALOG mode on a gateway that names its wire | **built** in `Gateway` (`wire` class attribute or a `Wire` port); the shipped wires opt in next — until then they keep PRD_12's catalog |
| groups, precedence, `override` / `bind`, build validation, the fallback access rule | **built** — `group`, `override`, `bind`, `verify`, `surface`, `build`; every §7 check that is wire-independent |
| name clashes per wire (one `operationId`, RPC method, gRPC method, tool twice) | the wire's `validate` — **built** for a project's `Wire` port; each shipped wire when it opts in |
| derived MCP hints and REST methods from facts | the facts are **built** (`Operation.is_query`, `idempotent`, `access`, response); deriving each wire's defaults from them is the wire's `derive`, when it opts in |
| the MCP wire | **built** — `McpGateway` sets `wire = "mcp"`, DECLARED by default, CATALOG on request; `McpWire` derives the tool name (`issue_invoice`: the DTO's name without `Command`/`Query`, snake_case — JSON-RPC's rule), `read_only` from a `Query`, `destructive` true on a write unless declared; prefixes a derived name by its group (`prefix`, else the alias) only when two operations answer it, never a declared one; refuses a name twice, a name outside `[A-Za-z0-9_-]{1,64}` (what the model APIs behind MCP clients accept), a Query declared destructive; publishes `title`, `readOnlyHint`, `destructiveHint`, `idempotentHint` (`@idempotency.once`), `openWorldHint` and a `DEPRECATED …` description prefix on `tools/list`. `build_mcp_server(bus)` / `Entrypoint(bus)` stay PRD_12's catalog with DTO class names (hints added); `build_mcp_server([buses])` is `McpGateway` in CATALOG. Tests: `tests/entrypoint/test_entrypoint_mcp.py` |
| `manifest()` | **built** — sorted, frozen, JSON-safe; CATALOG logs it at build |
| strict orphans (a binding for a wire no gateway of the process builds) | not built |
| `Deprecation` | the binding field and the sunset check are **built**; RFC 9745 / 8594 headers on each wire are not |
| the `Wire` port for a project's transport | **built** (`derive`, `validate`, `build`, optional `name_of`) |
| gRPC streaming kinds, JSON-RPC notifications, a server max timeout, versioning helpers | phase 3 (`RpcBinding.notification` is recorded, not served) |
| the fixed pipeline and stage decorators; `@idempotency.once` moved to a stage; `@caching.keeps` | not built here (caching) |
| the combinations refused at build | **built**: `@idempotency.once` on a `Query`, `@internal` with a binding, the fallback access rule, a binding per wire twice (at declaration). `@caching.keeps` on a non-Query waits for `@caching.keeps` |

## Decisions

### Declared by default, catalog on request

Encore, FastEndpoints and ASP.NET's fallback policy agree: where exposure is the risk, publication
is opt-in. The catalog stays — it is the right tool for an SDK — but a gateway asks for it by name
and logs what it publishes, so an automatic surface is never a surprise.

### Two axes: reachable, and who may call

Being on a wire (a binding) and being allowed (`@auth.requires` / `@auth.public`) are separate
declarations, as in Encore. The fallback rule joins them at the build: an operation that is
reachable **MUST** say who may call it.

### Decorators are data; the composition has the last word

A decorator next to `@auth.requires` and `@idempotency.once` puts the whole contract of a use case
in one place; the composition — `group`, `override`, `bind`, `exclude` — lets the same use case be
published differently per deployment without touching it. Both write one model, and the build
resolves it with a fixed precedence (§7).

### Keyed by type, path templates validated

Composition calls take the Command or handler class, never a name. A path template names fields
because that is REST's own concept; the build validates each one against the Command, so a rename
fails the build instead of a request.

## Open questions

1. ~~The default mode~~ — decided: DECLARED by default in this release; breaking is accepted.
2. ~~The layer in RPC names~~ — decided in PRD_15 §4: no layer in any public name.
3. ~~`Idempotency-Key` on REST~~ — decided in PRD_15 §2.3: the Command's `idempotency_key()` stays
   authoritative; the header is the key only when the Command has none.
4. **Profiles per deployment.** A file of `exclude`s per environment, keyed by import path and
   validated at build — needed, or is `exclude` in code enough?
