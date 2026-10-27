# sincpro_framework documentation

Documentation is organised by layer. Each folder is one layer of the framework: what it is for,
how it is designed, how to use it, and why it is the way it is. The root [README](../README.md) is
the tutorial; these pages are the reference and the reasoning behind it.

Upgrading a consumer after the persistence and event refactors? Start with
[events/upgrading.md](events/upgrading.md), including the Forge and MCP Odoo migration checks.

For the Forge-driven assessment of context composition, public contracts, entrypoint discovery
and durable delivery, see [PRD_25](prd/PRD_25_context-composition-and-durable-delivery.md).
It separates existing capabilities from proposed corrections and unverified acceptance criteria.

How an entity answers how it is read — its key, what a record brings, the order, the display
and the literal search, as `DEFAULT_*` class methods a caller's criteria overrides — and why
that left `presentation`: [PRD_27](prd/PRD_27_entity-default-reads.md).

## By problem

What the framework already has for the problems every service meets once it runs for real — none
needs a library of its own, each keeps its state where the context is kept (memory, or the store
every replica shares), and each says its failures in the one classification every wire answers
with:

| The problem | The piece | How it is used | Page |
|---|---|---|---|
| A dependency — SIAT, a payment gateway, another service — is down or slow, and every call waits out its timeout | `CircuitBreaker` | `with circuit:`, `@circuit`, `circuit.call(...)`, or handed to the adapter; a bus hosted elsewhere when asked: `?circuit_breaker=on` or `hosted_by(..., circuit_breaker=True)` | [core/circuit-breaker.md](core/circuit-breaker.md) |
| A job of several steps that must say how far it got, wait for a decision, or be tried again | `Pipeline` | `steps`, `always`, `retry = Retry()`, `Await(...)`, `lease =` | [core/pipelines.md](core/pipelines.md) |
| Work that must not run twice at once, on any replica | `Leases` | `with use_context().leases().held("name"):` | [core/leases.md](core/leases.md) |
| A write that a retry, a double click or a redelivered message must not repeat | `bus.idempotency`, `Idempotency` | `@billing.idempotency` on the use case — kept where the context is kept, nothing built; `.exactly_once(records)` for the answer in the write's transaction (`DatabaseIdempotencyRecords`); `Idempotency(store).once(...)` on a store of its own; `.run(key, ...)` by hand | [caching/README.md](caching/README.md#the-simple-way-buscache-and-busidempotency-nothing-to-build) |
| Something slow to compute or to fetch, with a fallback when its source fails | `bus.cache`, `Cache`, `QueryCaching` | `@billing.cache` on the function — kept where the context is kept; `.ttl`, `.sliding`, `.versioned`, `fail_safe=`; handed to an adapter; `billing.cache.queries(Query)` for a Query's answer; `Cache(store)` built with every strategy | [caching/README.md](caching/README.md#the-simple-way-buscache-and-busidempotency-nothing-to-build) |
| What a form keeps between requests, per user | Drafts | `self.context.drafts()` | [core/drafts.md](core/drafts.md) |
| See what a write would do before it happens | Dry run | `class CommandX(DryRunnable)`, `def preview`, `dry_run=True` | [core/dry-run.md](core/dry-run.md) |
| Store an object and publish it by an id — for a time, for a number of uses, to read or to upload | Object storage | `ObjectStorage(S3CompatibleObjectStore(...))`, `open`, `take`, `publish`, `check`, `redeem` | [object_storage/](object_storage/README.md) |
| A sign-in that lasts while it is used; a code used once | Sessions | `use_context().sessions().open({...})` | [core/sessions.md](core/sessions.md) |
| Whether a failure is the caller's, the business's or the service's — answer it, retry it, report it | `failure_kind` | declared once on the error; every wire, `Retry`, the queue and the observability read it | [core/exceptions.md](core/exceptions.md) |

## By layer

| Layer | Package | Start here | What is in it |
|---|---|---|---|
| **Version 4.0.0** | — | [version-4.md](version-4.md) | Exploration of ORM, Criteria, Entrypoints, Entity and events: production stays on 3.x until the architect is convinced; the spec is the essence, the style may change |
| **Specifications** | — | [spec/](spec/README.md) | What the framework covers and how far it has got, kept up to date in place, each capability with its status and the test that holds it: [persistence](spec/persistence.md) (the entity model, reading, analysis, writing, stores) [extensibility](spec/extensibility.md) (the requirements, how a module in another repository adds to an entity, and how releases are survived; the how-to is [persistence/extensions.md](persistence/extensions.md)) and [integration](spec/integration.md) (execution events and who hears them, the queue wire on any broker, the AWS adapters, settings sources, pipelines). A PRD is history; the specification is what is true today |
| **Start here** | — | [shapes.md](shapes.md) | What shape is your system — one database, a database per context, or the facts *are* the state — and what to wire for each |
| **Architecture** | the whole | [architecture/ARCHITECTURE.md](architecture/ARCHITECTURE.md) | The hexagonal layout, every component with its layer and dependencies, the flow of a request |
| **Core: the bus** | `sincpro_framework` | [core/](core/README.md) | [The execution path](core/execution-path.md) — what a call goes through and who owns each step; [pipelines](core/pipelines.md) — a job of steps and how far each run got; `UseFramework`, Features and ApplicationServices, dependencies, interceptors, error handling; the context — `use_context()` from anywhere, a tree from the process to each execution, threads, providers, stores, carried on every transport ([context-manager.md](core/context-manager.md)) |
| **Persistence** | `ddd`, `data_layer.orm` | [persistence/](persistence/README.md) | Aggregates, `Criteria` and `Specification`, relations, the SQLAlchemy adapter, the ledger that proves it, the decisions |
| **Event-driven** | `event_driven`, `event_driven.adapters.faststream` | [events/](events/README.md) | `DomainEvent`, `Publisher`, `Subscriber` made of buses, `SyncQueue` and `BackgroundQueue`; Kafka, RabbitMQ, Redis and NATS through FastStream (`[faststream]`), sync and async — [brokers.md](events/brokers.md) |
| **Crons** | `entrypoints.adapters.cron` | [cron/](cron/README.md) | `Crons` registry per bounded context, `Cron` classes with their buses injected, `CronProcess` in the background, one run per tick across replicas |
| **Process** | `entrypoints` (`Process`, `Poll`) | [process/](process/README.md) | The program that stays up and runs loops (`run` / `stop`): `CronGateway`, `Poll`, anything with those two methods. Not a wire under `entrypoints/` |
| **Migrations** | `data_layer.migrations`, `data_layer.orm` (`AlembicEngine`) | [migrations/](migrations/README.md) | Every context and store on one timeline: `ContextMigrations`, `Migrations`, `status` / `upgrade` / `downgrade --to`, `MigrationEngine` for any store, Alembic for SQL |
| **Data analysis** | `data_layer.data_analysis` | [data_analysis/](data_analysis/README.md) | A query read once: `QueryCache` holds each read by the repository's fingerprint, continues it page by page and narrows a complete `DataFrame` with no read; Parquet, Arrow and the hand-off to pandas, polars, DuckDB |
| **Runtime use cases** | `runtime.runtime_use_cases` | [runtime_use_cases/](runtime_use_cases/README.md) | Commands, Responses and a Feature or ApplicationService stored as source, loaded onto a new generation of the bus, checked and swapped in whole; `SqlUseCases` for a table the replicas share |
| **Caching** | `data_layer.store`, `data_layer.caching` | [caching/](caching/README.md) | `KeyValueStore` — six operations any provider has, and `replace`, which compares before it writes — with the store in memory and Redis/Valkey, Memcached and DynamoDB adapters (`[redis]`, `[memcached]`, `[aws]`); `Leases` on it ([core/leases.md](core/leases.md)); `bus.cache` / `bus.idempotency` (and the global `cache` / `idempotency`): kept where the context is kept, nothing built; `QueryCaching`: a Query's answer kept by the composition, tagged by what it read, let go of on commit or by domain events; `DatabaseIdempotencyRecords`: exactly once; `KeyValueRuns` for crons across replicas |
| **Auth** | `auth` | [auth/](auth/README.md) | Permissions declared on use cases and hooks (`@auth.requires`, `authenticated`, `public`, `when_denied=SKIP`), `AuthProvider` — the one contract a JWT issuer, an API key table or Odoo implements — `AccessControl` as the outermost guard of a bus and a gate of hooks, `security.as_identity` / `security.as_system`, `AuthProviderContract` and `granting` for tests; JSON-RPC, gRPC, MCP, an ASGI middleware and calls between services authenticate by themselves; `ApiKeyProvider`, `ServiceTokenProvider` |
| **Entrypoints** | `entrypoints` | [entrypoints/](entrypoints/README.md) | The bus catalog on every wire through one `Gateway` — REST with OpenAPI 3.1, JSON-RPC 2.0, gRPC, MCP — N buses automatically, `internal` and `include`/`exclude` to narrow, the app or server handed back |
| **Amazon Web Services** | `[aws]` adapters of every port | [aws/](aws/README.md) | DynamoDB as the store of everything shared, SNS / SQS / EventBridge to send events, `SqsBroker` and a Lambda to consume them through the queue wire, Parameter Store and Secrets Manager as settings sources — the simple way first, each option after |
| **Observability** | `observability` | [observability/](observability/README.md) | Traces, errors, logs and metrics: what works with no extra, what OpenTelemetry, Sentry and Prometheus add, the [metrics guide](observability/metrics.md) |
| **PRDs** | history | [prd/](prd/) | Why each layer was built the way it was at the time. Not edited to stay true: the [specifications](spec/README.md) are |

## Conventions

- Every page is in English; the code, docstrings and tests are too.
- A design page says *what*; a decisions page says *why* and what was rejected. Change the
  decision page when you change the design.
- `make docs` builds the generated wiki under `openwiki/` from the code. It references these
  pages; it does not replace them.
