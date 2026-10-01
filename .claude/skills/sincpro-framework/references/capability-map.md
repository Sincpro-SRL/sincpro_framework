# Capability map — one line per capability, and the owner

Use this to find the owner of a task. The **skill** column names a skill in this skill set, or a
reference of this router. The **package** column is the module inside `sincpro_framework/`, which
is installed with the package and is the source of truth. The **deep doc** column is a page in the
framework repo (`docs/…`, where every example runs as a test); it is not shipped with the package,
so read it only when the framework repo is at hand.

| Capability | Package | Skill | Deep doc (framework repo) |
|---|---|---|---|
| Bus, Feature, ApplicationService, DTO | `use_bus.py`, `sincpro_abstractions.py`, `bus.py` | `sincpro-framework` | README, `docs/core/README.md` |
| Dependency injection + typing | `use_bus.py` (`add_dependency`, `deps`), `ioc.py` | [bootstrap.md](bootstrap.md) | README "Recommended Infrastructure Structure" |
| Folder layout, repo variants | — | [module-structure.md](module-structure.md) | `docs/architecture/ARCHITECTURE.md` |
| Boundaries, `common/`, domain vs adapters | `testing/architecture.py` (the checks) | [context-boundaries.md](context-boundaries.md) | `docs/architecture/ARCHITECTURE.md` |
| Context manager (correlation, async handoff) | `context/` | `sincpro-framework-core` | `docs/core/context-manager.md` |
| Interceptors + `replaces=` | `interceptors.py` | `sincpro-framework-core` | `docs/core/interceptors.md` |
| Error handlers | `error_handler.py` | `sincpro-framework-core` | README "Error Handling" |
| Failure classification (`DomainError`, `failure_kind`) | `ddd/exceptions.py`, `transport/failures.py` | `sincpro-framework-entrypoints` | `docs/entrypoints/README.md` |
| Settings / configuration per context | `settings/`, `conf/` | `sincpro-framework-settings` | `docs/core/settings.md` |
| Aggregates, tables, repository, UoW | `ddd/`, `orm/` | `sincpro-framework-persistence` | `docs/persistence/guide.md` |
| Criteria, Specification, pagination, count | `ddd/criteria/`, `ddd/query.py` | `sincpro-framework-criteria` | `docs/persistence/criteria.md`, `specification.md` |
| Relations (FK / id-list / bus / function) | `ddd/entity/relations.py`, `orm/` | `sincpro-framework-persistence` | `docs/persistence/relations.md` |
| Hooks + archive/audit/change-tracking mixins | `ddd/repositories/hooks.py`, `ddd/entity/mixins/` | `sincpro-framework-persistence` | `docs/persistence/hooks.md`, `lifecycle.md` |
| Domain events (record/publish/subscribe) | `ddd/events.py`, `events/` | `sincpro-framework-domain-events` | `docs/events/README.md` |
| Change tracking | `ddd/entity/mixins/` | `sincpro-framework-domain-events` | `docs/events/change-tracking.md` |
| Event sourcing + event store | `orm/` (`event_columns`) | `sincpro-framework-domain-events` | `docs/shapes.md` §3 |
| Outbox + relay | `ddd/events.py` (`EventTrackableMixin`), `orm/` (`delivery_columns`) | `sincpro-framework-domain-events` | `docs/events/change-tracking.md` |
| Brokers (Kafka/RabbitMQ/Redis/NATS) | `events/faststream/` | `sincpro-framework-domain-events` | `docs/events/brokers.md` |
| Cache + idempotency | `caching/` | `sincpro-framework-caching` | `docs/caching/README.md` |
| Traces / errors / logs / metrics | `observability/` | `sincpro-framework-observability` | `docs/observability/` |
| Entrypoints (REST/FastAPI/RPC/gRPC/MCP/queue) | `entrypoints/` | `sincpro-framework-entrypoints` | `docs/entrypoints/` |
| Bounded contexts across services | `remote_execution/` | `sincpro-framework-entrypoints` | `docs/entrypoints/bounded-contexts-across-services.md` |
| Introspection (registries of a built bus) | `introspection/` | `sincpro-framework-entrypoints` | `docs/entrypoints/README.md` |
| Auth (permissions, providers) | `auth/` | `sincpro-framework-auth` | `docs/auth/README.md` |
| Crons | `cron/` | `sincpro-framework-operations` | `docs/cron/README.md` |
| Migrations across contexts | `migrations/` | `sincpro-framework-operations` | `docs/migrations/README.md` |
| Data analysis (Arrow/pandas/polars/DuckDB) | `data_analysis/` | `sincpro-framework-analytics` | `docs/data_analysis/README.md` |
| Runtime use cases (source on the bus) | `runtime_use_cases/` | `sincpro-framework-analytics` | `docs/runtime_use_cases/README.md` |
| Workflows (Commands composed as JSON) | `workflows/` | `sincpro-framework-analytics` | `docs/workflows/README.md` |
| System shapes (one DB / DB per context / facts as state) | — | `sincpro-framework-persistence`, `sincpro-framework-domain-events` | `docs/shapes.md` |
| Testing utilities | `testing/` | [testing.md](testing.md) | `docs/persistence/testing.md` |
