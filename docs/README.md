# sincpro_framework documentation

Documentation is organised by layer. Each folder is one layer of the framework: what it is for,
how it is designed, how to use it, and why it is the way it is. The root [README](../README.md) is
the tutorial; these pages are the reference and the reasoning behind it.

| Layer | Package | Start here | What is in it |
|---|---|---|---|
| **Start here** | — | [shapes.md](shapes.md) | What shape is your system — one database, a database per context, or the facts *are* the state — and what to wire for each |
| **Architecture** | the whole | [architecture/ARCHITECTURE.md](architecture/ARCHITECTURE.md) | The hexagonal layout, every component with its layer and dependencies, the flow of a request |
| **Core: the bus** | `sincpro_framework` | [core/](core/README.md) | `UseFramework`, Features and ApplicationServices, dependencies, the context manager, interceptors, error handling |
| **Persistence** | `ddd`, `orm` | [persistence/](persistence/README.md) | Aggregates, `Criteria` and `Specification`, relations, the SQLAlchemy adapter, the ledger that proves it, the decisions |
| **Events** | `events`, `events.faststream` | [events/](events/README.md) | `DomainEvent`, `Publisher`, `Subscriber` made of buses, `SyncQueue` and `BackgroundQueue`; Kafka, RabbitMQ, Redis and NATS through FastStream (`[faststream]`), sync and async — [brokers.md](events/brokers.md) |
| **Crons** | `cron` | [cron/](cron/README.md) | `Crons` registry per bounded context, `Cron` classes with their buses injected, `CronProcess` in the background, one run per tick across replicas |
| **Migrations** | `migrations`, `orm.migrations` | [migrations/](migrations/README.md) | Every context and store on one timeline: `ContextMigrations`, `Migrations`, `status` / `upgrade` / `downgrade --to`, `MigrationEngine` for any store, Alembic for SQL |
| **Data analysis** | `data_analysis` | [data_analysis/](data_analysis/README.md) | A query read once: `QueryCache` holds each read by the repository's fingerprint, continues it page by page and narrows a complete `DataFrame` with no read; Parquet, Arrow and the hand-off to pandas, polars, DuckDB |
| **Runtime use cases** | `runtime_use_cases` | [runtime_use_cases/](runtime_use_cases/README.md) | Commands, Responses and a Feature or ApplicationService stored as source, loaded onto a new generation of the bus, checked and swapped in whole; `SqlUseCases` for a table the replicas share |
| **Workflows (experimental)** | `workflows` | [workflows/](workflows/README.md) | Commands composed as JSON — `execute`, `code`, `for_each`, `fail`, `when` — validated against the live bus, run with a trace, drawn for a node editor |
| **Caching** | `caching` | [caching/](caching/README.md) | `KeyValueStore` — six operations any provider has — with the store in memory and Redis/Valkey and Memcached adapters (`[redis]`, `[memcached]`); `QueryCaching`: a Query's answer kept by the composition, tagged by what it read, let go of on commit or by domain events; `KeyValueRuns` for crons across replicas |
| **Auth** | `auth` | [auth/](auth/README.md) | Permissions declared on use cases and hooks (`@auth.requires`, `authenticated`, `public`, `when_denied=SKIP`), `AuthProvider` — the one contract a JWT issuer, an API key table or Odoo implements — `AccessControl` as the outermost guard of a bus and a gate of hooks, `as_identity` / `as_system`, `AuthProviderContract` and `granting` for tests; JSON-RPC, gRPC, MCP, an ASGI middleware and calls between services authenticate by themselves; `ApiKeyProvider`, `ServiceTokenProvider` |
| **Entrypoints** | `entrypoints` | [entrypoints/](entrypoints/README.md) | The bus catalog on every wire through one `Gateway` — REST with OpenAPI 3.1, JSON-RPC 2.0, gRPC, MCP — N buses automatically, `internal` and `include`/`exclude` to narrow, the app or server handed back |
| **Observability** | `observability` | [observability/](observability/README.md) | Tracing and errors: what works with no extra, what OpenTelemetry and Sentry add, the two doors |
| **PRDs** | history and proposals | [prd/](prd/) | The requirement documents each layer was built from, and the proposals for what comes next (PRD_04–09) |

## Conventions

- Every page is in English; the code, docstrings and tests are too.
- A design page says *what*; a decisions page says *why* and what was rejected. Change the
  decision page when you change the design.
- `make docs` builds the generated wiki under `openwiki/` from the code. It references these
  pages; it does not replace them.
