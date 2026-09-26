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
| **Events** | `events` | [events/](events/README.md) | `DomainEvent`, `Publisher`, `Subscriber` made of buses, `SyncQueue` and `BackgroundQueue` |
| **Entrypoints** | `entrypoints` | [entrypoints/](entrypoints/README.md) | The bus catalog as JSON-RPC 2.0 methods and as MCP tools, with a real use case |
| **Observability** | `observability` | [observability/](observability/README.md) | Tracing and errors: what works with no extra, what OpenTelemetry and Sentry add, the two doors |
| **PRDs** | history | [prd/](prd/) | The product requirement documents the typed container, middleware and observability were built from |

## Conventions

- Every page is in English; the code, docstrings and tests are too.
- A design page says *what*; a decisions page says *why* and what was rejected. Change the
  decision page when you change the design.
- `make docs` builds the generated wiki under `openwiki/` from the code. It references these
  pages; it does not replace them.
