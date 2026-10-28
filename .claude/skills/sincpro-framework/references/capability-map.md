# Capability map — one line per capability, and the owner

Use this to find the owner of a task. The **skill** column names a skill in this skill set, or a
reference of this router. The **package** column is the module inside `sincpro_framework/`, which
is installed with the package and is the source of truth. The **deep doc** column is a page in the
framework repo (`docs/…`, where every example runs as a test); it is not shipped with the package,
so read it only when the framework repo is at hand.

| Capability | Package | Skill | Deep doc (framework repo) |
|---|---|---|---|
| Bus, Feature, ApplicationService, DTO | `use_bus.py`, `sincpro_abstractions.py`, `bus.py` | `sincpro-framework` | README, `docs/core/README.md` |
| Registry of one process | `registry.py` | [bootstrap.md](bootstrap.md) | `docs/registry/README.md` |
| A process and the loops it runs | `entrypoints/entrypoint/workers.py` | `sincpro-framework-operations` | `docs/process/README.md` |
| Dependency injection + typing | `use_bus.py` (`add_dependency`, `deps`), `ioc.py` | [bootstrap.md](bootstrap.md) | README "Recommended Infrastructure Structure" |
| Folder layout, repo variants | — | [module-structure.md](module-structure.md) | `docs/architecture/ARCHITECTURE.md` |
| Boundaries, `common/`, domain vs adapters | `runtime/testing/analysis/architecture.py` (the checks) | [context-boundaries.md](context-boundaries.md) | `docs/architecture/ARCHITECTURE.md` |
| The context: `use_context()`, the tree of levels, threads, providers, stores, propagation, execution identity | `context/` | `sincpro-framework-context` | `docs/core/context-manager.md` |
| Interceptors + `replaces=` | `bus_pipeline/interceptors/` | `sincpro-framework-core` | `docs/core/interceptors.md` |
| CircuitBreaker breaker for an adapter or a bus hosted elsewhere (`CircuitBreaker`, `CircuitOpen`) | `circuit_breaker/` | `sincpro-framework-core` | `docs/core/circuit-breaker.md` |
| Error handlers | `error_handler.py` | `sincpro-framework-core` | README "Error Handling" |
| Object storage: objects by key on any store, and publications of them by id — time, uses, read or write; no URL — open, take once, expiring objects, upload-then-claim with one id, bytes whole to process or by parts to pass on (`stream`, `direct`: a proxy that holds one part, or the storage's own URL) (`ObjectStorage`, `ObjectStore`, `S3CompatibleObjectStore`, `LocalObjectStore`, `ObjectRef`, `StoredObject`, `ObjectStream`, `Publication`, `Grant`, `Access`, `Refusal`) — not filesystems (NFS, SMB) | `data_layer/object_storage/` | [capability-map.md](capability-map.md) | `docs/object_storage/README.md` |
| Dry run: a Command that says what it would do (`DryRunnable`, `preview`, `Preview`) | `sincpro_abstractions.py`, `bus.py` | `sincpro-framework-core` | `docs/core/dry-run.md` |
| Failure classification and the problem every wire draws (`DomainError`, `failure_kind`, `Problem`, `NextStep`, `problem_of`, `refine_problem`) | `ddd/exceptions.py`, `common/failures/` | `sincpro-framework-entrypoints` | `docs/core/exceptions.md` |
| Settings / configuration per context | `settings/`, `conf/` | `sincpro-framework-settings` | `docs/core/settings.md` |
| Aggregates, tables, repository, UoW | `ddd/`, `data_layer/orm/` | `sincpro-framework-persistence` | `docs/persistence/guide.md` |
| Repository capabilities, aggregate views, shared UoW | `ddd/repositories/`, `data_layer/orm/sqlalchemy/` | `sincpro-framework-persistence` | `docs/persistence/reference.md`, `docs/persistence/transactions.md` |
| Transactional numbering | `ddd/repositories/numbering.py`, `data_layer/orm/sqlalchemy/adapters/numbering.py` | `sincpro-framework-persistence` | `docs/persistence/guide.md` |
| Criteria, Specification, pagination, count | `ddd/criteria/`, `ddd/query.py` | `sincpro-framework-criteria` | `docs/persistence/criteria.md`, `specification.md` |
| Relations (FK / id-list / bus / function) | `ddd/entity/relations.py`, `data_layer/orm/` | `sincpro-framework-persistence` | `docs/persistence/relations.md` |
| Hooks + archive/audit/change-tracking mixins | `ddd/repositories/hooks.py`, `ddd/entity/mixins/` | `sincpro-framework-persistence` | `docs/persistence/hooks.md`, `lifecycle.md` |
| Domain events (record/publish/subscribe) | `ddd/events/`, `event_driven/` | `sincpro-framework-domain-events` | `docs/events/README.md` |
| Change tracking | `ddd/entity/mixins/` | `sincpro-framework-domain-events` | `docs/events/change-tracking.md` |
| Event sourcing + event store | `ddd/entity/mixins/event_sourced.py`, `data_layer/orm/` (`template_table.event_table`, `map_events`) | `sincpro-framework-domain-events` | `docs/shapes.md` §3 |
| Outbox + relay | `ddd/events/mixins/`, `event_driven/entrypoint/relay.py` (facade), `event_driven/services/relaying.py`, `entrypoints/adapters/cron/registry.py` | `sincpro-framework-domain-events` | `docs/events/change-tracking.md` |
| Brokers (Kafka/RabbitMQ/Redis/NATS) | `event_driven/adapters/faststream/`, `entrypoints/adapters/queue/` | `sincpro-framework-domain-events` | `docs/events/brokers.md` |
| Cache + idempotency | `data_layer/caching/` | `sincpro-framework-caching` | `docs/caching/README.md` |
| Traces / errors / logs / metrics | `observability/` | `sincpro-framework-observability` | `docs/observability/` |
| Entrypoints (REST/FastAPI/RPC/gRPC/MCP/queue) | `entrypoints/` | `sincpro-framework-entrypoints` | `docs/entrypoints/` |
| Bounded contexts across services | `remote_execution/` | `sincpro-framework-entrypoints` | `docs/entrypoints/bounded-contexts-across-services.md` |
| Introspection (registries of a built bus) | `introspection/` | `sincpro-framework-entrypoints` | `docs/entrypoints/README.md` |
| Auth (permissions, providers) | `auth/` | `sincpro-framework-auth` | `docs/auth/README.md` |
| Crons | `entrypoints/adapters/cron/` | `sincpro-framework-operations` | `docs/cron/README.md` |
| Migrations across contexts | `data_layer/migrations/` | `sincpro-framework-operations` | `docs/migrations/README.md` |
| Data analysis (Arrow/pandas/polars/DuckDB) | `data_layer/data_analysis/` | `sincpro-framework-analytics` | `docs/data_analysis/README.md` |
| Runtime use cases (source on the bus) | `runtime/runtime_use_cases/` | `sincpro-framework-analytics` | `docs/runtime_use_cases/README.md` |
| Extensions of an entity you do not own | `ddd/entity/extensions.py`, `data_layer/orm/sqlalchemy/services/extension_*.py` | `sincpro-framework-extensions` | `docs/persistence/extensions.md`, `docs/spec/extensibility.md` |
| System shapes (one DB / DB per context / facts as state) | — | `sincpro-framework-persistence`, `sincpro-framework-domain-events` | `docs/shapes.md` |
| Testing utilities | `runtime/testing/` | [testing.md](testing.md) | `docs/persistence/testing.md` |
