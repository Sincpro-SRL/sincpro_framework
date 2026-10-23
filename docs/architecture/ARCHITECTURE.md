# Architecture

The Sincpro Framework is a hexagonal architecture with DDD and CQRS. One `UseFramework` per bounded
context replaces the application layer with a bus that routes each DTO to the Feature or
ApplicationService that answers it. This page is the map: what each component is for and where it
sits. Each one has its own page; none is restated here.

The previous text of this page described version 2.4.1. Its history is in git.

## The shape

```text
 OUTSIDE ──► ENTRYPOINTS ──► THE BUS, one per bounded context ──► ADAPTERS ──► OUTSIDE
             REST · RPC       the context of the call              repositories, stores
             gRPC · MCP       providers · interceptors             queues and brokers
             a queue consumer handler · error handlers            other services
             the clock        observation: observability, outcomes
             a worker
             a Subscriber               │
                                  THE DOMAIN
                       DTOs · Entities · Criteria · DomainEvents
```

- **An entrypoint** starts the application layer from outside and opens the flow.
- **An adapter** is what the framework calls outward.
- **The bus** is where every call goes through the same path, whoever started it: see
  [the execution path](../core/execution-path.md).
- **A handler instance serves every execution**: what belongs to one request lives in local
  variables, never on `self`.
- **A bounded context is isolated.** One bus reaches another as a dependency, or over the network
  when the other is hosted by another service.

## The components

| Package | What it is | Where to read |
|---|---|---|
| `sincpro_abstractions.py`, `use_bus.py`, `bus.py`, `ioc.py`, `dependencies.py` | The bus: `DataTransferObject`, `Feature`, `ApplicationService`, registering, building, routing and running a call; the typed dependencies | [core](../core/README.md), [features](../core/features.md), [extending a bus](../core/extending.md) |
| `context/` | The flow of a call: who and for which tenant, a tree from the process to each execution, the execution's identity, providers, stores, drafts | [context](../core/context-manager.md), [drafts](../core/drafts.md) |
| `bus_pipeline/` | The three layers on the path of a call: what wraps a handler (interceptors, control), and what is announced about it when it begins (incomes) and when it ends (outcomes), both observation | [interceptors](../core/interceptors.md), [incomes](../core/incomes.md), [outcomes](../core/outcomes.md) |
| `pipelines/` | An ApplicationService that is the order of its steps (`Pipeline`), and the interceptor the bus attaches to keep each run in the context | [pipelines](../core/pipelines.md) |
| `error_handler.py` | The error chains of the three layers | [error handlers](../core/error-handlers.md), [exceptions](../core/exceptions.md) |
| `observability/` | The signals sent to external backends: traces, errors, logs, metrics | [observability](../observability/README.md) |
| `ddd/`, `data_layer/orm` | The domain vocabulary — entities, `Criteria`, relations, `DomainEvent` — and the stores that persist it | [persistence](../persistence/README.md) |
| `event_driven/` | Delivering events: `Publisher`, queues, `Subscriber`, the relay | [events](../events/README.md) |
| `entrypoints/` | Everything that starts the application layer from outside: the wires, the clock, workers | [entrypoints](../entrypoints/README.md), [cron](../cron/README.md), [process](../process/README.md) |
| `remote_execution/` | A bounded context executed by another service that runs the same code | [entrypoints](../entrypoints/README.md) |
| `auth/` | Permissions declared on use cases, the provider contract | [auth](../auth/README.md) |
| `data_layer/caching`, `data_layer/migrations`, `data_layer/data_analysis` | A key-value store, query caching and idempotency; the schema timeline of every context; reads kept as frames | [caching](../caching/README.md), [migrations](../migrations/README.md), [data analysis](../data_analysis/README.md) |
| `settings/` | One document resolved into typed shapes | [settings](../core/settings.md) |
| `introspection/` | Reads a built bus's registries; what entrypoints build their catalogs on | [entrypoints](../entrypoints/README.md) |
| `runtime/` | Use cases loaded as source, the doubles and checks for a project's tests | [runtime use cases](../runtime_use_cases/README.md) |
| `aio/` | The async facade of a bus (`get_async_bus`) | [core](../core/README.md) |

## Two kinds of hook on the path

What hangs on the execution path is control or observation, with opposite contracts: control may
change the answer and observation never does. The table of which piece is which, and in which step
of a call, is in [the execution path](../core/execution-path.md).

## Where decisions live

- [Specifications](../spec/README.md) say what is true today, kept up to date in place.
- A design page says *what*; its decisions page says *why* and what was rejected.
- [PRDs](../prd/) are history: why a layer was built the way it was at the time.
