---
name: sincpro-framework
description: Program any bounded context with sincpro_framework (UseFramework, Feature, ApplicationService, DataTransferObject). Use whenever you add a use case, a bounded context, a dependency, an aggregate, a repository, a Criteria query, a domain event, an entrypoint or observability to a Sincpro Python service (sincpro_mcp_odoo, sincpro_payments_sdk, sincpro_siat_soap, or a new one). It explains the framework's context, vocabulary and architecture, and the mistakes to avoid (such as registering use cases in entrypoints/). This is the router; read it first, then the layer skill it points at.
---

# sincpro-framework

The application layer of every Sincpro Python service: one `UseFramework` per bounded context, a
`Feature`/`ApplicationService` per use case, both routed by the DTO they answer. Without
configuring anything, every call on the bus gets a span, an error report on every unexpected
exception, and typed dependency injection.

This skill stands alone: it explains what the framework is, its vocabulary and the architecture of a
service, then routes to the layer skill. The framework repo also carries deep docs (`docs/…` in the
framework repo); they are not shipped with the package, and nothing here depends on them.

> Verified against **v3.15.0** (main at 07fc0ce). Production stays on 3.x; 4.0.0 is an
> exploration (`docs/version-4.md`).
> A version string alone does not establish these APIs: verify the installed artifact and
> lockfile before upgrading a consumer. Skills from main can be newer than its dependency.

## Context

`sincpro_framework` is the **application layer** of a service built with DDD and hexagonal
architecture. A service is one or more **bounded contexts**; each one owns a bus, its use cases,
its vocabulary and the adapters it talks through. The bus is the only door into a context: an
HTTP route, an MCP tool, a cron, a test and another context all execute the same DTO on it.

What it is not:

- **Not a web framework.** FastAPI, JSON-RPC, gRPC, MCP and queues are optional *wires* that read
  the bus (`sincpro-framework-entrypoints`). The domain never imports them.
- **Not an ORM.** Persistence is a port (`Repository`) with SQLAlchemy behind an optional extra
  (`sincpro-framework-persistence`).
- **Not a service locator.** Dependencies are registered once per context and injected as
  attributes; a use case never fetches the bus or a client from a global.
- **Not a place for transport logic.** No status codes, request objects or exception handlers in
  `services/` — errors are classified once by the framework.

## Abstractions

| Term | What it is | Kind | Import |
|---|---|---|---|
| `UseFramework` (the bus) | One instance per bounded context. Registers handlers by DTO, holds dependencies, runs each call with span, error report and context. Called as `bus(dto, Response)`. | class | `from sincpro_framework import UseFramework` |
| Bounded context | A vocabulary with its own lifecycle: a folder with its own bus, `infrastructure/` and `services/`. | convention | — |
| `Feature` | One atomic use case. `@bus.feature(Command)` on a class with `execute(self, dto)`. Talks to adapters and domain. | base class | `from sincpro_framework import Feature` |
| `ApplicationService` | Composes two or more Features of the **same** bus that succeed or fail together, through `self.feature_bus`. `@bus.app_service(Command)`. | base class | `from sincpro_framework import ApplicationService` |
| `DataTransferObject` | Pydantic model for everything that crosses the bus: Commands, Queries, Responses. | base class | `from sincpro_framework import DataTransferObject` |
| Command / Response naming | `Command<VerbNoun>` in, `Response<VerbNoun>` out. Sincpro convention; the framework routes by class and never requires the prefix. | convention | — |
| `Query` | DTO for a read: carries `criteria: Criteria`. Answered with `ResponsePaginatedQuery`. | base class | `from sincpro_framework.ddd import Query, ResponsePaginatedQuery` |
| `DependencyContextType` | Per-context typing mixin declaring each injected attribute (`token_adapter: TokenAdapter`). Never instantiated; mixed into the context's `Feature`/`ApplicationService` bases. | convention | defined in `infrastructure/dependencies.py` |
| Dependencies registry | `bus.add_dependency("name", instance)` → `self.name` inside a handler, `bus.deps.name` outside. Registering a name twice raises. | method | on `UseFramework` |
| `feature_bus` / `app_service_bus` | The two halves of the root bus. `self.feature_bus` is injected into every ApplicationService and executes **Features only**; `app_service_bus` is internal and never injected — an ApplicationService cannot call another one. | attribute | `from sincpro_framework.bus import FeatureBus` (typing only) |
| `Entity` / aggregate | `@dataclass` with `id`, `created_at`, `updated_at`. The aggregate is the Entity a `Repository` reads and writes as one unit. Not a DTO. | base class | `from sincpro_framework.ddd import Entity` |
| `IRepository` | The read/write aggregate port. `Analyzes`, `WritesInBulk` and `Transacts` are separate capabilities; the SQL adapter is `orm.Repository`. | port | `from sincpro_framework.ddd import IRepository` |
| `AggregateRepository[T]` | Typed view of one aggregate over a repository; `get(id)`, `search(criteria)`, `save(aggregate)`. SQL-specific view: `orm.DatabaseAggregateRepository[T]`. | adapter view | `from sincpro_framework.ddd import AggregateRepository` |
| `DomainEvent` | A fact that happened. An `Entity` subclass with correlation/causation ids, recorded on an aggregate (`record`) and drained (`pull_events`). | base class | `from sincpro_framework.ddd import DomainEvent` |
| `DomainError` + `failure_kind` | Base of every expected refusal. A subclass declares `failure_kind = FailureKind.NOT_FOUND` (…) and every wire maps it to its code. | exception | `from sincpro_framework.ddd import DomainError`; `from sincpro_framework.common.failures import FailureKind` |
| Adapter | A class in `adapters/` that wraps an external system or a replaceable mechanism, registered as a dependency. | convention | — |
| Port | A `typing.Protocol` the use cases call (a Feature, an ApplicationService or the domain), in `domain/`, when there are 2+ implementations or a test double is a real consumer. A contract only an adapter calls — the one a facade holds its implementations by — lives in that adapter's folder, not in `domain/`. | convention | `from typing import Protocol` |
| Gateway / entrypoint | A wire that reads one or more buses and publishes their use cases (REST, JSON-RPC, gRPC, MCP, queue). Built in `entrypoints/`; never registers a use case. | class | `from sincpro_framework.entrypoints import Gateway` (base) |
| Exposure decorators | `@rest.get(...)`, `@rpc()`, `@grpc()`, `@mcp()`, `@queue(...)`, `@internal` on the existing handler in `services/`. Import no transport library. | decorator | `from sincpro_framework.entrypoints import rest, rpc, grpc, mcp, queue, internal` |

## Architecture

### Layers and dependency direction

| Layer | Holds | May import |
|---|---|---|
| `domain/` | DTOs shared by several use cases, aggregates, value objects, ports (`Protocol`) the use cases call, pure rules, policy constants | its own `domain/`, a lower context's `domain/`, `sincpro_framework.ddd` |
| `adapters/` | One class per external system or replaceable mechanism (client, repository impl, classifier) | `domain/`; never another adapter |
| `services/` | One file per use case: DTOs + `@bus.feature`/`@bus.app_service` handler | `domain/`, the context package (for `bus`, `Feature`), other services' **DTOs only** |
| `settings.py` (context root) | The context's settings shape and its object, built at `<pkg>.<ctx>` | `common/settings.py`, the framework |
| `infrastructure/` | Wiring: `framework.py`, `dependencies.py`, tables, logger | everything inside the context |
| `entrypoints/` | Gateways and, at most, hand-written routes that translate and call the bus | the buses and their DTOs; nothing imports `entrypoints/` |

Dependencies point inward: `entrypoints → bus → services → domain`, with `adapters` implementing
what `domain` declares and `infrastructure` choosing which adapter each context gets. Contexts form
an acyclic graph; `common/` is the foundation and imports no sibling context.
`sincpro_framework.runtime.testing.layer_violations("my_service")` checks these rules in a test.

### Canonical tree of a multi-context service

```
my_service/
  exceptions.py  conf/my_service.yml    # process-wide, no domain; the one settings document
  domains/
    common/                              # shared kernel (bus optional)
      settings.py                        # SharedSettings: what every context inherits
      domain/  adapters/  infrastructure/  services/  __init__.py
    billing/
      settings.py                        # BillingSettings(SharedSettings) + its object
      __init__.py                        # bus = config_billing_framework(...); then services
      infrastructure/framework.py        # typed Feature / ApplicationService bases
      infrastructure/dependencies.py     # BillingDependencyContextType + register_dependencies
      services/__init__.py               # imports every use-case module
      services/issue_invoice.py          # CommandIssueInvoice + ResponseIssueInvoice + IssueInvoice
      domain/invoice.py                  # only when there is vocabulary
      adapters/tax_authority.py          # only when it talks to its own system
  entrypoints/                           # optional: one process exposing the buses
    http/app.py                          # FastApiGateway({...}).app()
    mcp_server.py
```

Variants (`apps/<ctx>/`, one context at the package root) and where a new file goes:
[references/module-structure.md](references/module-structure.md). What may cross a context
boundary and what earns a place in `common/`:
[references/context-boundaries.md](references/context-boundaries.md).

### Bootstrap order (load-bearing)

1. **Bus instance** — `billing = config_billing_framework("billing")` in the context `__init__.py`
   (creates `UseFramework`, registers dependencies).
2. **`from . import services`** — decorators register against that instance at import time.
3. **Things that need the handlers** — `auth.on(billing)`, interceptors, error handlers,
   `ignore_sentry_exceptions(...)`.
4. **Gateways last** — adding a bus to a gateway builds it (so does its first execution); a built
   bus refuses every later registration with `BusAlreadyBuilt`.

Full skeleton: [references/bootstrap.md](references/bootstrap.md).

### How a call flows

```
HTTP / MCP / RPC / cron / test / another context
        │  Command DTO
        ▼
  gateway (entrypoints/)  ── auth, Idempotency-Key header → context, failure kind → status
        │  bus(Command, Response)
        ▼
  UseFramework (bus) ── context, interceptors, span, error report
        ├── FeatureBus ──────────► Feature.execute(dto)        (@idempotency.once wraps it here)
        └── ApplicationServiceBus ► ApplicationService.execute(dto)
                                         └─ self.feature_bus(ChildCommand, ChildResponse) → Feature
        ▼
  self.<adapter> (injected) ──► repository / external API / mechanism
        ▼
  Response DTO back up the same path
```

## The non-negotiables

1. **Every use case is a `Feature` or an `ApplicationService` on its context's bus.** Never a
   homemade registry, a class with `run()`, or a function that takes the bus as an argument.
2. **Reuse a use case by executing its DTO on the injected bus.** From another service import only
   its DTOs; never a function, a factory or the handler class.
3. **`domain/` is vocabulary and pure rules, and every rule lives on the type it constrains.** A
   function there that takes an adapter is I/O wearing a domain name; shared I/O is an adapter
   registered in `dependencies.py`. A pure rule is a method of its model — never a module-level
   `def` (see [references/hard-rules.md](references/hard-rules.md)).
4. **Inject named components (classes) in `infrastructure/dependencies.py`.** Never a `lambda`.
5. **One bus per bounded context**, created *before* `services/` is imported.
6. **`self` holds only injected dependencies.** One handler instance serves every call on every
   thread; request data lives in locals. `self.context` is per call.
7. **`entrypoints/` never registers a use case or creates a bus.** It builds gateways.

Reasoning and review checklist: [references/hard-rules.md](references/hard-rules.md).

## Mistakes an agent makes

Silent ones first: the top rows work in a demo and fail in production without raising anything.

| Mistake | Why it hurts | Do this instead |
|---|---|---|
| Registering a Feature/ApplicationService or a `UseFramework` inside `entrypoints/` to get a route | A second surface: MCP, RPC and tests never see it, and it exists only if that module was imported | Decorate the existing handler in `services/` with `@rest.post(...)`/`@mcp()`; `entrypoints/` only builds the gateway (`sincpro-framework-entrypoints`) |
| Writing `@app.exception_handler(...)` or catching to build a JSON error | Two classifications drift; the gateway's RFC 9457 problems, logging and Sentry rules are bypassed | Subclass `DomainError`, declare `failure_kind`; raise it from the Feature |
| Calling `Feature().execute(dto)` or `handler.execute(...)` directly, in code or tests | No span, no Sentry event, no context, no interceptors, no auth; dependencies are missing | `bus(Command(...), Response)`; `self.feature_bus(...)` inside an ApplicationService |
| Importing a Feature class (or a helper function) from another service | Bypasses the bus: no span, no error report, no interceptors | Import that use case's DTOs and execute them on the bus |
| A function `def x(feature_bus, ...)` or `def x(client, ...)` exported from `services/` or `domain/` | Hidden dispatcher; orchestration nobody traces | Execute the Command; shared I/O is an adapter |
| Writing request data to `self` in `execute` | Concurrent calls read each other's data | Locals; pass values as arguments |
| Registering after the first execution or after a gateway was built | `BusAlreadyBuilt` at import or at startup | Follow the bootstrap order above |
| An ApplicationService calling another ApplicationService | `feature_bus` only knows Features: `UnknownDTOToExecute` at runtime | Compose Features; or make the outer use case the only ApplicationService |
| Swallowing an exception in an error handler that only logs | The handler's return becomes the answer: `None` | Re-raise to delegate (`sincpro-framework-core`) |
| Reaching into another context's `services/` or adapters instead of its bus | Couples to internals; the context cannot be extracted as a service | `add_dependency("common", common)` and `self.common(Command(...), Response)` |
| A module-level `def` in `domain/` (`check_tenant_id(...)`, `parse_address(...)`, `entry_of(event)`) | Nobody can tell which model owns the rule; the next service re-implements it with a twist | A method on the model: `Tenant.check_id(...)` (`@staticmethod`), `HostAddress.parse(...)` (`@classmethod`), `invoice.post()` (instance) |
| Two aggregates in one `domain/` file | Files stop naming concepts; imports and reviews get tangled | One aggregate per file, named after it (`tenant.py`, `user.py`) |
| Empty `domain/`/`adapters/` folders, `utils/`, `helpers/`, `use_cases/` | Noise the next agent copies | Create a folder only when something goes in it |

## The one decision: what artifact is this?

Ask in order. Stop at the first yes.

1. **Pure vocabulary / rule, no I/O?** → in `domain/` (or `common/domain/` when two contexts need
   it and neither owns it), **on the type it constrains**: a method of the entity, aggregate or
   value object (`@staticmethod` for a validation, `@classmethod` for a factory), or a new value
   object when several fields travel together. Never a loose module-level function.
2. **I/O against an external system, or a replaceable mechanism?** → named adapter in `adapters/`,
   registered in `dependencies.py`; typed against a port when a double is a real consumer.
3. **One atomic operation?** → `Feature`: one file in `services/` with the Command, the Response,
   `@bus.feature(Command)` and `class VerbNoun(Feature)`.
4. **Two or more use cases that must succeed or fail together?** → `ApplicationService`, executing
   child Commands on `self.feature_bus`.
5. **A whole new vocabulary with its own lifecycle?** → new bounded context (own bus).
6. **A new way to reach existing use cases (route, tool, consumer)?** → exposure decorator on the
   handler + a gateway in `entrypoints/` — never a new use case.

Anything that fits none of these: stop and ask. Do not invent a fourth kind of service.

## Where each layer lives

| I want to… | Read |
|---|---|
| Know where a file goes, pick the repo variant | [references/module-structure.md](references/module-structure.md) |
| Decide a cross-context import, `common/`, domain vs adapters | [references/context-boundaries.md](references/context-boundaries.md) |
| The context: `use_context()`, levels, threads, providers, required keys, stores, propagation | `sincpro-framework-context` |
| Interceptors, `replaces=`, error handlers, opening `bus.context` | `sincpro-framework-core` |
| Aggregate, table, repository, unit of work, hooks, mixins | `sincpro-framework-persistence` |
| Filter, order, page, count, aggregate, relations | `sincpro-framework-criteria` |
| Domain events, outbox, event sourcing, brokers | `sincpro-framework-domain-events` |
| Cache a Query's answer, idempotency | `sincpro-framework-caching` |
| Traces, errors, logs, metrics | `sincpro-framework-observability` |
| Configuration, secrets, environment | `sincpro-framework-settings` |
| REST / FastAPI / RPC / gRPC / MCP / queue, context map | `sincpro-framework-entrypoints` |
| Permissions, identity, providers | `sincpro-framework-auth` |
| Crons, a process and its loops, migrations | `sincpro-framework-operations` |
| DataFrames, runtime use cases, workflows | `sincpro-framework-analytics` |
| Test a use case | [references/testing.md](references/testing.md) |
| Everything, one line each | [references/capability-map.md](references/capability-map.md) |

## Verify before calling it done

```bash
make format          # black + isort (isort does not reorder __init__.py)
make test            # the use case is exercised through its bus with doubled adapters
make verify-format   # the gate
```

Run `pyright` too when the project uses it.

## References

- [references/hard-rules.md](references/hard-rules.md) — the non-negotiables, with the incident each prevents
- [references/bootstrap.md](references/bootstrap.md) — the skeleton, import order, and `DependencyContextType`
- [references/module-structure.md](references/module-structure.md) — folder layout, repo variants, where a new file goes
- [references/context-boundaries.md](references/context-boundaries.md) — dependency direction, `common/`, domain vs adapters
- [references/testing.md](references/testing.md) — testing through the bus, doubles, architecture checks
- [references/capability-map.md](references/capability-map.md) — one line per capability and its owner
